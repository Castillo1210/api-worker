# app/services/schema_registry.py
import hashlib
import json
import redis.asyncio as redis
from typing import Dict, List, Type, Optional, Any, Callable
from pydantic import BaseModel, Field, create_model
from datetime import date, datetime
from decimal import Decimal
from app.services.cloudsql_client import CloudSQLClient
from app.config import get_settings
import structlog

logger = structlog.get_logger()


class SchemaRegistry:
    def __init__(self):
        self.settings = get_settings()
        self.db = CloudSQLClient()
        self.redis: Optional[redis.Redis] = None
        
        # Cache local (fallback si Redis falla)
        self._local_cache: Dict[str, Any] = {}
        self._cache_ttl = 300  # 5 min
        
        # Modelos cacheados
        self._voucher_schema: Optional[Type[BaseModel]] = None
        self._response_schema: Optional[Type[BaseModel]] = None
        self._business_rules: List[Dict] = []
        self._computed_fns: Dict[str, Callable] = {}
        self._schema_hash: str = ""
    
    async def initialize(self):
        """Inicializar conexiones"""
        await self.db.connect()
        self.redis = redis.from_url(
            self.settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=True
        )
        # Suscribirse a canal de invalidación
        pubsub = self.redis.pubsub()
        await pubsub.subscribe("schema:invalidate")
        # Task en background para escuchar
        import asyncio
        asyncio.create_task(self._listen_invalidation(pubsub))
    
    async def _listen_invalidation(self, pubsub):
        async for message in pubsub.listen():
            if message["type"] == "message" and message["data"] == "schema_changed":
                logger.info("Invalidación de schema recibida vía Redis")
                self._invalidate_local_cache()
    
    def _invalidate_local_cache(self):
        self._voucher_schema = None
        self._response_schema = None
        self._business_rules = []
        self._computed_fns = {}
        self._schema_hash = ""
        self._local_cache.clear()
    
    async def get_voucher_schema(self) -> Type[BaseModel]:
        """Retorna modelo Pydantic para request a LlamaCloud"""
        current_hash = await self._compute_schema_hash()
        
        if self._voucher_schema and current_hash == self._schema_hash:
            return self._voucher_schema
        
        # Cache miss o schema cambió
        fields = await self._load_schema_fields()
        self._voucher_schema = self._build_pydantic_model("VoucherSchema", fields)
        self._schema_hash = current_hash
        await self._cache_schema(current_hash, self._voucher_schema)
        return self._voucher_schema
    
    async def get_response_schema(self) -> Type[BaseModel]:
        """Modelo para respuesta de LlamaCloud (incluye confidence, etc.)"""
        if self._response_schema and self._schema_hash:
            return self._response_schema
        
        base = await self.get_voucher_schema()
        # Añadir campos de respuesta
        extra_fields = {
            "confidence": (float, Field(ge=0.0, le=1.0, description="Confianza global")),
            "field_confidences": (Dict[str, float], Field(default_factory=dict, description="Confianza por campo")),
            "raw_response": (Optional[Dict[str, Any]], Field(default=None, description="Respuesta cruda"))
        }
        self._response_schema = create_model("LlamaParserResponse", __base__=base, **extra_fields)
        return self._response_schema
    
    async def get_business_rules(self) -> List[Dict]:
        if self._business_rules:
            return self._business_rules
        
        rules = await self._load_business_rules()
        self._business_rules = rules
        return rules
    
    async def get_computed_functions(self) -> Dict[str, Callable]:
        if self._computed_fns:
            return self._computed_fns
        
        computed = await self._load_computed_fields()
        self._computed_fns = computed
        return computed
    
    async def _compute_schema_hash(self) -> str:
        """Hash SHA256 de todos los componentes del schema"""
        fields = await self._load_schema_fields()
        rules = await self._load_business_rules()
        computed = await self._load_computed_fields()
        
        content = json.dumps({
            "fields": fields,
            "rules": rules,
            "computed": computed
        }, sort_keys=True, default=str)
        return hashlib.sha256(content.encode()).hexdigest()[:16]
    
    async def _load_schema_fields(self) -> List[Dict]:
        key = "schema:voucher_fields"
        # 1. Redis
        if self.redis:
            cached = await self.redis.get(key)
            if cached:
                return json.loads(cached)
        # 2. Local
        if key in self._local_cache:
            return self._local_cache[key]
        # 3. BD
        await self.db.connect()
        async with self.db.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT field_name, field_type, description, is_required, 
                    default_value, field_order, is_computed, compute_expression
                FROM voucher_schema_fields
                WHERE is_active = true
                ORDER BY field_order
            """)
        fields = [dict(r) for r in rows]
        # Cache
        if self.redis:
            await self.redis.setex(key, self._cache_ttl, json.dumps(fields, default=str))
        self._local_cache[key] = fields
        return fields
    
    async def _load_business_rules(self) -> List[Dict]:
        key = "schema:business_rules"
        if self.redis:
            cached = await self.redis.get(key)
            if cached:
                return json.loads(cached)
        if key in self._local_cache:
            return self._local_cache[key]
        
        await self.db.connect()
        async with self.db.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT rule_name, condition_expression, error_message, severity
                FROM voucher_business_rules
                WHERE is_active = true
            """)
        rules = [dict(r) for r in rows]
        if self.redis:
            await self.redis.setex(key, self._cache_ttl, json.dumps(rules, default=str))
        self._local_cache[key] = rules
        return rules
    
    async def _load_computed_fields(self) -> Dict[str, Callable]:
        key = "schema:computed_fields"
        if self.redis:
            cached = await self.redis.get(key)
            if cached:
                # Deserializar lambdas (simple eval - solo trusted)
                return {k: eval(v) for k, v in json.loads(cached).items()}
        if key in self._local_cache:
            return self._local_cache[key]
        
        await self.db.connect()
        async with self.db.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT field_name, depends_on, compute_expression, field_type
                FROM voucher_computed_fields
                WHERE is_active = true
            """)
        
        computed = {}
        for row in rows:
            # Crear función lambda segura
            expr = row["compute_expression"]
            deps = [d.strip() for d in row["depends_on"].split(",")]
            # Compilar lambda: lambda monto, moneda: expr
            fn = eval(f"lambda {', '.join(deps)}: {expr}")  # Solo trusted internal
            computed[row["field_name"]] = fn
        
        if self.redis:
            # Serializar lambdas como strings
            serializable = {k: v.__code__.co_code.hex() for k, v in computed.items()}
            await self.redis.setex(key, self._cache_ttl, json.dumps(serializable))
        self._local_cache[key] = computed
        return computed
    
    def _build_pydantic_model(self, name: str, fields: List[Dict]) -> Type[BaseModel]:
        """Crea modelo Pydantic dinámico desde campos BD"""
        field_defs = {}
        type_map = {
            "float": float,
            "int": int,
            "str": str,
            "bool": bool,
            "date": date,
            "datetime": datetime,
            "optional[str]": Optional[str],
            "optional[float]": Optional[float],
            "optional[int]": Optional[int],
            "list[str]": List[str],
        }
        
        for f in fields:
            fname = f["field_name"]
            ftype_str = f["field_type"].lower()
            ftype = type_map.get(ftype_str, str)
            required = f["is_required"]
            default = f.get("default_value")
            desc = f.get("description", "")
            
            if ftype_str.startswith("optional[") or not required:
                ftype = Optional[ftype]
                if default is None:
                    default = None
            
            if required and default is None:
                field_defs[fname] = (ftype, Field(..., description=desc))
            else:
                field_defs[fname] = (ftype, Field(default=default, description=desc))
        
        return create_model(name, **field_defs)
    
    async def _cache_schema(self, hash_val: str, model: Type[BaseModel]):
        """Cache en Redis"""
        if self.redis:
            await self.redis.setex(
                f"schema:model:{hash_val}",
                self._cache_ttl,
                hash_val  # Solo guardamos hash, modelo en memoria
            )
    
    async def invalidate(self):
        """Llamar cuando BD cambia (admin ejecuta)"""
        self._invalidate_local_cache()
        if self.redis:
            await self.redis.publish("schema:invalidate", "schema_changed")
            # Limpiar keys
            keys = await self.redis.keys("schema:*")
            if keys:
                await self.redis.delete(*keys)
    
    async def close(self):
        if self.redis:
            await self.redis.close()
        await self.db.close()