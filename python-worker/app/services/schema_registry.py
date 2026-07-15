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
        self._voucher_schemas: Dict[str, Type[BaseModel]] = {}
        self._response_schemas: Dict[str, Type[BaseModel]] = {}
        self._business_rules: List[Dict] = []
        self._computed_fns: Dict[str, Callable] = {}
        self._schema_hashes: Dict[str, str] = {}
    
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
        self._voucher_schemas = {}
        self._response_schemas = {}
        self._business_rules = []
        self._computed_fns = {}
        self._schema_hashes = {}
        self._local_cache.clear()
    
    async def get_voucher_schema(self, banco_id: str) -> Type[BaseModel]:
        """Retorna modelo Pydantic para request a LlamaCloud, especifico del banco"""
        current_hash = await self._compute_schema_hash(banco_id)
        
        if self._voucher_schemas.get(banco_id) and current_hash == self._schema_hashes.get(banco_id):
            return self._voucher_schemas[banco_id]
        
        # Cache miss o schema cambió
        fields = await self._load_schema_fields(banco_id)
        model = self._build_pydantic_model(f"VoucherSchema_{banco_id}", fields)
        self._voucher_schemas[banco_id] = model
        self._schema_hashes[banco_id] = current_hash
        await self._cache_schema(current_hash, model)
        return model
    
    async def get_response_schema(self, banco_id: str) -> Type[BaseModel]:
        """Modelo para respuesta de LlamaCloud (incluye confidence, etc.)"""
        if self._response_schemas.get(banco_id) and self._schema_hashes.get(banco_id):
            return self._response_schemas[banco_id]
        
        base = await self.get_voucher_schema(banco_id)
        # Añadir campos de respuesta
        extra_fields = {
            "confidence": (Optional[float], Field(default=None, ge=0.0, le=1.0, description="Confianza global")),
            "field_confidences": (Dict[str, float], Field(default_factory=dict, description="Confianza por campo")),
            "raw_response": (Optional[Dict[str, Any]], Field(default=None, description="Respuesta cruda"))
        }
        model = create_model(f"LlamaParserResponse_{banco_id}", __base__=base, **extra_fields)
        self._response_schemas[banco_id] = model
        return model
    
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
    
    async def _compute_schema_hash(self, banco_id: str) -> str:
        """Hash SHA256 de todos los componentes del schema, por banco"""
        fields = await self._load_schema_fields(banco_id)
        
        content = json.dumps({
            "fields": fields
        }, sort_keys=True, default=str)
        return hashlib.sha256(content.encode()).hexdigest()[:16]
    
    async def _load_schema_fields(self, banco_id: str) -> List[Dict]:
        key = f"schema:voucher_fields:{banco_id}"

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
                SELECT field_name, field_type, description, is_required, field_order
                FROM voucher_schema_fields
                WHERE is_active = true AND banco_id = $1
                ORDER BY field_order
            """, banco_id)

        fields = [dict(r) for r in rows]
        # Cache
        if self.redis:
            await self.redis.setex(key, self._cache_ttl, json.dumps(fields, default=str))
        self._local_cache[key] = fields
        return fields
    
    async def _load_business_rules(self) -> List[Dict]:
        return []
    
    async def _load_computed_fields(self) -> Dict[str, Callable]:
        return []
    
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