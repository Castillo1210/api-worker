import asyncpg
from typing import Optional, List
from uuid import UUID
from datetime import date, datetime
from decimal import Decimal
from app.config import get_settings
from app.models.deposit import DepositRow, DepositUpdateData, ValidationResult
import structlog

logger = structlog.get_logger()

class CloudSQLClient:
    def __init__(self):
        self.settings = get_settings()
        self.pool: Optional[asyncpg.Pool] = None

    async def connect(self):
        self.pool = await asyncpg.create_pool(
            host=self.settings.DATABASE_HOST,
            port=self.settings.DATABASE_PORT,
            database=self.settings.DATABASE_NAME,
            user=self.settings.DATABASE_USER,
            password=self.settings.DATABASE_PASSWORD,
            min_size=self.settings.DATABASE_POOL_MIN,
            max_size=self.settings.DATABASE_POOL_MAX,
            command_timeout=30,
        )
        logger.info("CloudSQL pool creado", min=self.settings.DATABASE_POOL_MIN, max=self.settings.DATABASE_POOL_MAX)

    async def close(self):
        if self.pool:
            await self.pool.close()
            logger.info("CloudSQL pool cerrado")

    async def get_deposit_for_processing(self, deposit_id: str) -> Optional[DepositRow]:
        """Obtiene depósito con datos necesarios para procesamiento"""
        if not self.pool:
            raise RuntimeError("Pool no inicializado")
        
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT 
                    d."Id" as id,
                    d."ImagenVoucher" as imagen_voucher,
                    d."Monto" as monto,
                    d."Moneda" as moneda,
                    d."FechaDeposito" as fecha_deposito,
                    d."NumeroOperacion" as numero_operacion,
                    d."Estado" as estado
                FROM depositos d
                WHERE d."Id" = $1
            """, deposit_id)

            if row:
                return dict(row)
            return None
        
    async def lookup_empresa_id(self, beneficiario: str) -> Optional[UUID]:
        """Busca empresa_id por nombre beneficiario (fuzzy ILIKE)"""
        if not self.pool or not beneficiario:
            return None
        
        async with self.pool.acquire() as conn:
            # Buscar por nombre o razon_social
            row = await conn.fetchrow("""
                SELECT id FROM empresas
                WHERE "Nombre" ILIKE $1
                LIMIT 1""", f"%{beneficiario}%")
            
            if row:
                empresa_id = row["id"]
                return empresa_id
            
            logger.warning("Empresa no encontrada", beneficiario=beneficiario)
            return None
        
    async def check_duplicate_operacion(self, numero_operacion: str, exclude_id: Optional[str] = None) -> bool:
        """Verifica unicidad de numero_operacion"""
        if not self.pool:
            raise RuntimeError("Pool no inicializado")
        
        async with self.pool.acquire() as conn:
            if exclude_id:
                count = await conn.fetchval(
                    """SELECT COUNT(*) FROM depositos WHERE "NumeroOperacion" = $1 AND "Id" != $2""",
                    numero_operacion, exclude_id
                )
            else:
                count = await conn.fetchval(
                    """SELECT COUNT(*) FROM depositos WHERE "NumeroOperacion" = $1""",
                    numero_operacion
                )
            return count > 0
        
    async def update_deposit(self, deposit_id: str, data: DepositUpdateData) -> bool:
        """Actualiza campos del depósito"""
        if not self.pool:
            raise RuntimeError("Pool no inicializado")
        
        field_map = {
            "Monto": data.monto,
            "Moneda": data.moneda,
            "FechaDeposito": data.fecha_deposito,
            "NumeroOperacion": data.numero_operacion,
            "Estado": data.estado,
        }
        
        fields = []
        values = [deposit_id]
        param_idx = 2

        for field, value in field_map.items():
            if value is not None:
                fields.append(f"{field} = ${param_idx}")
                values.append(value)
                param_idx += 1
        
        if not fields:
            return False

        query = f"UPDATE depositos SET {', '.join(fields)} WHERE \"Id\" = $1"

        async with self.pool.acquire() as conn:
            result = await conn.execute(query, *values)
            success = result.startswith("UPDATE")
            logger.info("Depósito actualizado", deposit_id=deposit_id, estado=data.estado, result=result)
            return success

        logger.info("Depósito actualizado", deposit_id=deposit_id, estado=estado)

    async def update_deposit_status_only(self, deposit_id: str, estado: str, motivo_rechazo: Optional[str] = None) -> bool:
        """Actualiza solo estado y el motivo rechazo"""
        if not self.pool:
            raise RuntimeError("Pool no inicializado")
        
        async with self.pool.acquire() as conn:
            if motivo_rechazo:
                await conn.execute(
                    """UPDATE depositos SET "Estado" = $1, "MotivoRechazo" = $2 WHERE "Id" = $3""",
                    estado, motivo_rechazo, deposit_id
                )
            else:
                await conn.execute(
                    """UPDATE depositos SET "Estado" = $1 WHERE "Id" = $2""", 
                    estado, deposit_id
                )
            
            return True
        
    async def get_bank_prompt(self, banco_id: str) -> Optional[str]:
        """Retorna solo el texto del prompt (string)"""
        if not self.pool:
            raise RuntimeError("Pool no inicializado")
        
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT prompt
                FROM bank_prompts
                WHERE banco_id = $1 AND is_active = true
                LIMIT 1""", banco_id)
            return row["prompt"] if row else None
        
    async def get_active_schema_fields(self) -> List[dict]:
        if not self.pool:
            raise RuntimeError("Pool no inicializado")
        
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT field_name, field_type, description, is_required, field_order
                FROM voucher_schema_fields
                WHERE is_active = true
                ORDER BY field_order""")
            return [dict(r) for r in rows]