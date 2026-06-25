# app/services/business_validator.py
from typing import List, Dict, Any
from app.services.cloudsql_client import CloudSQLClient
import structlog

logger = structlog.get_logger()


class BusinessValidator:
    """Validaciones de negocio post-IA (hardcodeadas por ahora, movibles a BD después)"""
    def __init__(self, db: CloudSQLClient):
        self.db = db
        self._error_cache: Dict[str, str] = {} # error_code -> UUID
    
    async def validate(self, data: Dict[str, Any]) -> Dict[str, Any]:
        error_ids = []
        warning_ids = []
        
        # Monto > 0
        monto = data.get("monto")
        if monto is None:
            error_ids.append(await self._get_error_id("MONTO_FALTANTE"))
        elif monto <= 0:
            error_ids.append(await self._get_error_id("MONTO_CERO"))

        # Beneficiario requerido
        beneficiario = data.get("beneficiario")
        if beneficiario is None:
            error_ids.append(await self._get_error_id("BENEFICIARIO_FALTANTE"))
        
        # Moneda normalizada
        moneda = data.get("moneda")
        if not moneda:
            error_ids.append(await self._get_error_id("MONEDA_FALTANTE"))
        elif str(moneda).upper() not in ("PEN", "USD"):
            error_ids.append(await self._get_error_id("MONEDA_INVALIDAD"))
        
        # Numero operacion
        nro_op = data.get("numero_operacion")
        if not nro_op or not str(nro_op).strip():
            error_ids.append(await self._get_error_id("NUMERO_OPERACION_FALTANTE"))
        
        # Fecha
        fecha = data.get("fecha_operacion")
        if fecha:
            try:
                from datetime import date, datetime, timedelta
                fecha_obj = datetime.strptime(str(fecha), "%Y-%m-%d").date()
                if fecha_obj > date.today():
                    error_ids.append(await self._get_error_id("FECHA_FUTURA"))
                elif fecha_obj < date.today().replace(year=date.today().year - 2):
                    error_ids.append(await self._get_error_id("FECHA_MUY_ANTIGUA"))
                elif fecha_obj < date.today() - timedelta(days=7):
                    error_ids.append(await self._get_error_id("FECHA_RECIENTE"))
            except ValueError:
                error_ids.append(await self._get_error_id("FECHA_INVALIDA"))
        else:
            error_ids.append(await self._get_error_id("FECHA_FALTANTE"))
        
        is_valid = len(error_ids) == 0
        requires_review = len(warning_ids) > 0
        
        logger.info("Validación negocio completada", is_valid=is_valid, error_ids=error_ids, requires_review=requires_review)
        return {
            "error_ids": error_ids,
            "warning_ids": warning_ids,
            "is_valid": is_valid,
            "requires_review": requires_review
        }
    
    async def _get_error_id(self, error_code: str) -> str:
        """Obtiene UUID del error_code desde BD con cache"""
        if error_code in self._error_cache:
            return self._error_cache[error_code]
        
        if not self.db.pool:
            raise RuntimeError("DB pool no inicializado")
        
        async with self.db.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id FROM voucher_business_errors WHERE error_code = $1 AND is_active = true",
                error_code
            )
            if row:
                error_id = str(row["id"])
                self._error_cache[error_code] = error_id
                return error_id
            
        raise ValueError(f"Error code no encontrado en BD: {error_code}")