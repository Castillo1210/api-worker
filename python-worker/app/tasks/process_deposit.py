# app/tasks/process_deposit.py
import asyncio
from celery import shared_task
from uuid import UUID
from datetime import datetime, time, date
from app.config import get_settings
from app.models.deposit import DepositRow, DepositUpdateData, ValidationResult
from app.services.cloudsql_client import CloudSQLClient
from app.services.storage_client import StorageClient
from app.services.llama_parser_client import LlamaParserClient, LlamaParserError
from app.services.prompt_selector import PromptSelector
from app.services.business_validator import BusinessValidator
from app.services.callback_client import CallbackClient
from app.utils.redis_queue_client import RedisQueueClient
from uuid import UUID
from app.services.metrics import (
    deposit_processing_total,
    deposit_processing_duration_seconds,
    llama_parser_calls_total,
    llama_parser_duration_seconds,
    db_updates_total,
    callback_notifications_total,
)
import structlog

logger = structlog.get_logger()

# Instancias globales (reutilizadas entre tasks)
_db: CloudSQLClient = None
_storage: StorageClient = None
_llama: LlamaParserClient = None
_prompt: PromptSelector = None
_callback: CallbackClient = None
_business_validator: BusinessValidator = None
_redis_queue: RedisQueueClient = None


def _init_services():
    global _db, _storage, _llama, _prompt, _callback, _business_validator, _redis_queue
    if _db is None:
        _db = CloudSQLClient()
        _storage = StorageClient()
        _llama = LlamaParserClient(_db)
        _business_validator = BusinessValidator(_db)
        _redis_queue = RedisQueueClient()
        _callback = CallbackClient()

@shared_task(
    bind=True,
    max_retries=3,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    acks_late=True,
    reject_on_worker_lost=True
)

def process_deposit(self, deposit_id: str):
    """"
    Task principal: Consumido de Redis Queue, procesa depósito completo.
    """
    _init_services()

    start_time = time.time()

    try:
        result = asyncio.run(_process_deposit_async(deposit_id))

        duration = time.time() - start_time
        deposit_processing_duration_seconds.observe(duration)
        deposit_processing_total.labels(status=result["status"]).inc()

        return result
    except Exception as e:
        duration = time.time() - start_time
        deposit_processing_duration_seconds.observe(duration)
        deposit_processing_total.labels(status="error").inc()
        raise

async def _process_deposit_async(deposit_id: str) -> dict:
    # 1. Conectar BD
    await _db.connect()
    
    try:
        # 2. Obtener depósito de BD
        deposit = await _db.get_deposit_for_processing(deposit_id)
        if not deposit:
            return {"status": "error", "error_type": "not_found", "error_message": "Depósito no encontrado", "deposit_id": deposit_id}
        
        logger.info("Depósito cargado", deposit_id=deposit_id)
        
        # 3. Verificar que tiene imagen
        if not deposit.imagen_voucher:
            return {"status": "error", "error_type": "no_image", "error_message": "Depósito sin imagen_voucher", "deposit_id": deposit_id}
        
        # 4. Descargar archivo de GCS
        file_bytes = _storage.download_voucher(deposit.imagen_voucher)
        content_type = _storage.get_content_type(deposit.imagen_voucher)
        file_type = "pdf" if "pdf" in content_type else "image"
        
        logger.info("Archivo descargado", deposit_id=deposit_id, file_type=file_type, size=len(file_bytes))

        # Extraer con schema dinámico (sin prompt)
        llama_data = await _llama.extract(file_bytes, file_type)

        data_dict = llama_data.model_dump()

        validation = await _business_validator.validate(data_dict)

        # Determinar estado
        if validation["error_ids"]:
            estado = "rechazado"
        elif validation["warning_ids"]:
            estado = "requiere_revisión"
        else:
            estado = "validado"

        from datetime import datetime

        # Lookup empresa_id
        empresa_id = None
        if data_dict.get("beneficiario"):
            empresa_id = await _db.lookup_empresa_id(data_dict["beneficiario"])
        
        # 10. Preparar datos para actualización
        update_data = DepositUpdateData(
            monto=data_dict.get("monto"),
            moneda=data_dict.get("moneda", "PEN"),
            fecha_deposito=data_dict.get("fecha_operacion"),
            numero_operacion=data_dict.get("numero_operacion", ""),
            numero_operacion_banco=data_dict.get("numero_operacion"),
            empresa_id=empresa_id,
            cliente=data_dict.get("cliente"),
            datos_ocr=data_dict,
            estado=estado,
            fecha_validacion=datetime.utcnow().isoformat() if estado == "validado" else None,
            error_ids=validation["error_ids"],
            warning_ids=validation["warning_ids"]
        )
        
        # 11. Actualizar BD
        success = await _db.update_deposit(deposit_id, update_data)
        
        if not success:
            raise RuntimeError("Falló actualización BD")
        
        # PUBLICAR RESULTADO EN REDIS
        await _redis_queue.publish_result({
            "deposit_id": deposit_id,
            "status": estado,
            "error_ids": [str(e) for e in validation["error_ids"]],
            "warning_ids": [str(w) for w in validation["warning_ids"]],
            "error_type": None,
            "error_message": None
        })

        logger.info("Resultado publicado en Redis", deposit_id=deposit_id, estado=estado)
        
        return {
            "status": estado,
            "deposit_id": deposit_id,
            "error_ids": [str(e) for e in validation["error_ids"]],
            "warning_ids": [str(w) for w in validation["warning_ids"]]
        }
    except LlamaParserError as e:
        return await _handle_llama_failure(deposit_id, e.error_code, str(e))
    except Exception as e:
        logger.error("Error inesperado", deposit_id=deposit_id, error=str(e), exc_info=True)
        return {"status": "error", "error_type": "unexpected", "error_message": str(e), "deposit_id": deposit_id}
    finally:
        await _db.close()

async def _handle_llama_failure(deposit_id: str, error_code: str, error_message: str) -> dict:
    """Error en LlamaCloud - Update BD + Notify minimal"""
    logger.error("LlamaCloud failure", deposit_id=deposit_id, error_code=error_code, error=error_message)
    
    # 1. Update BD estado rechazado
    await _db.update_deposit_status_only(deposit_id, "error_ia", f"Error IA: {error_message}")
    
    # 2. Publicar en Redis para que API Bridge notifique
    await _redis_queue.publish_result({
        "deposit_id": deposit_id,
        "status": "error_ia",
        "error_ids": [await _get_system_error_id(error_code)],
        "warning_ids": [],
        "error_type": error_code,
        "error_message": f"Error IA: {error_message}"
        
    })
    
    return {"status": "error", "error_type": error_code, "error_message": f"Error IA: {error_message}", "deposit_id": deposit_id}

async def _get_system_error_id(error_code: str) -> str:
    """Obtiene UUID para errores del sistema (IA_TIMEOUT, IA_ERROR_500, etc...)"""
    if not _db.pool:
        raise RuntimeError("DB pool no inicializado")
    
    async with _db.pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id FROM voucher_business_errors WHERE error_code = $1 AND is_active = true",
            error_code
        )
        if row:
            return str(row["id"])
    raise ValueError(f"System error code no encontrado: {error_code}")