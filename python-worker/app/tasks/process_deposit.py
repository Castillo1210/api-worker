import asyncio
import time

import structlog
from celery import shared_task

from app.services.business_validator import BusinessValidator
from app.services.callback_client import CallbackClient
from app.services.cloudsql_client import CloudSQLClient
from app.services.llama_parser_client import LlamaParserClient, LlamaParserError
from app.services.metrics import deposit_processing_duration_seconds, deposit_processing_total
from app.services.storage_client import StorageClient
from app.utils.redis_queue_client import RedisQueueClient

logger = structlog.get_logger()

# Instancias globales (reutilizadas entre tasks)
_db: CloudSQLClient = None
_storage: StorageClient = None
_llama: LlamaParserClient = None
_callback: CallbackClient = None
_business_validator: BusinessValidator = None
_redis_queue: RedisQueueClient = None


def _init_services():
    global _db, _storage, _llama, _callback, _business_validator, _redis_queue
    if _db is None:
        _db = CloudSQLClient()
        _storage = StorageClient()
        _llama = LlamaParserClient(cloud_sql_client=_db)
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
    reject_on_worker_lost=True,
)
def process_deposit(self, deposit_id: str):
    """
    Procesa el depósito hasta obtener la extracción de LlamaCloud
    y publica el resultado en Redis para que otro consumidor continúe.
    """
    _init_services()

    start_time = time.time()

    try:
        result = asyncio.run(_process_deposit_async(deposit_id))

        duration = time.time() - start_time
        deposit_processing_duration_seconds.observe(duration)
        deposit_processing_total.labels(status=result["status"]).inc()

        return result
    except Exception:
        duration = time.time() - start_time
        deposit_processing_duration_seconds.observe(duration)
        deposit_processing_total.labels(status="error").inc()
        raise


async def _process_deposit_async(deposit_id: str) -> dict:
    await _db.connect()

    try:
        deposit = await _db.get_deposit_for_processing(deposit_id)
        if not deposit:
            payload = {
                "deposit_id": deposit_id,
                "status": "error",
                "error_type": "not_found",
                "error_message": "Depósito no encontrado",
            }
            await _redis_queue.publish_result(payload)
            return payload

        logger.info("Depósito cargado", deposit_id=deposit_id)

        if not deposit.imagen_voucher:
            payload = {
                "deposit_id": deposit_id,
                "status": "error",
                "error_type": "no_image",
                "error_message": "Depósito sin imagen_voucher",
            }
            await _redis_queue.publish_result(payload)
            return payload

        file_bytes = _storage.download_voucher(deposit.imagen_voucher)
        content_type = _storage.get_content_type(deposit.imagen_voucher)
        file_type = "pdf" if "pdf" in content_type else "image"

        logger.info("Archivo descargado", deposit_id=deposit_id, file_type=file_type, size=len(file_bytes))

        llama_data = await _llama.extract(file_bytes, file_type)
        data_dict = llama_data.model_dump()

        validation = await _business_validator.validate(data_dict)

        if validation["error_ids"]:
            estado = "rechazado"
        elif validation["warning_ids"]:
            estado = "requiere_revisión"
        else:
            estado = "validado"

        empresa_id = None
        if data_dict.get("beneficiario"):
            empresa_id = await _db.lookup_empresa_id(data_dict["beneficiario"])

        payload = {
            "deposit_id": deposit_id,
            "status": estado,
            "error_ids": [str(e) for e in validation["error_ids"]],
            "warning_ids": [str(w) for w in validation["warning_ids"]],
            "error_type": None,
            "error_message": None,
            "llama_data": data_dict,
            "empresa_id": str(empresa_id) if empresa_id else None,
        }

        await _redis_queue.publish_result(payload)

        logger.info("Resultado publicado en Redis", deposit_id=deposit_id, estado=estado)
        return payload

    except LlamaParserError as e:
        payload = {
            "deposit_id": deposit_id,
            "status": "error_ia",
            "error_ids": [],
            "warning_ids": [],
            "error_type": e.error_code,
            "error_message": f"Error IA: {str(e)}",
        }
        await _redis_queue.publish_result(payload)
        return payload

    except Exception as e:
        logger.error("Error inesperado", deposit_id=deposit_id, error=str(e), exc_info=True)
        payload = {
            "deposit_id": deposit_id,
            "status": "error",
            "error_type": "unexpected",
            "error_message": str(e),
        }
        await _redis_queue.publish_result(payload)
        return payload

    finally:
        await _db.close()
