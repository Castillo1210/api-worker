import asyncio
import time

import structlog
from celery import shared_task
from uuid import UUID
from app.config import get_settings
from app.models.deposit import DepositRow, DepositUpdateData, ValidationResult
from app.services.cloudsql_client import CloudSQLClient
from app.services.storage_client import StorageClient
from app.services.llama_parser_client import LlamaParserClient, LlamaParserError
from app.utils.redis_queue_client import RedisQueueClient
from app.services.schema_registry import SchemaRegistry
from app.services.metrics import (
    deposit_processing_total,
    deposit_processing_duration_seconds,
)

logger = structlog.get_logger()

# Instancias globales (reutilizadas entre tasks)
_db: CloudSQLClient = None
_storage: StorageClient = None
_llama: LlamaParserClient = None
_redis_queue: RedisQueueClient = None


def _init_services():
    global _db, _storage, _llama, _redis_queue
    if _db is None:
        _db = CloudSQLClient()
        _storage = StorageClient()
        _llama = LlamaParserClient(SchemaRegistry(), _db)
        _redis_queue = RedisQueueClient()


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

async def _process_deposit_async(deposit_id: str):
    """Extrae campos con IA, actualiza BD, publica en Redis.
    No decide estados de negocio. Siempre pone 'procesado' cualquiera fuera el resultado."""

    # 1. Conectar BD
    await _db.connect()

    try:
        deposit = await _db.get_deposit_for_processing(deposit_id)
        if not deposit or not deposit.get("imagen_voucher"):
            return {"status": "error", "error_type": "no_image"}
        
        logger.info("Depósito cargado", deposit_id=deposit_id)
        
        # 4. Descargar archivo de GCS
        file_bytes = _storage.download_voucher(deposit.imagen_voucher)
        content_type = _storage.get_content_type(deposit.imagen_voucher)
        file_type = "pdf" if "pdf" in content_type else "image"

        logger.info("Archivo descargado", deposit_id=deposit_id, file_type=file_type, size=len(file_bytes))

        try:
            # Extraer con schema dinámico (sin prompt)
            llama_data = await _llama.extract(file_bytes, file_type)
        except LlamaParserError as e:
            # IA falló -> actualizar estado a "procesado" y publicar error
            await _db.update_deposit_status_only(deposit_id, "procesado")
            await _redis_queue.publish_result({
                "deposit_id": deposit_id,
                "status": "error_ia",
                "error_type": e.error_code,
                "error_message": str(e)
            })
            return {"status": "error_ia"}
        
        # 10. Preparar datos para actualización
        update_data = DepositUpdateData(
            monto=llama_data.get("monto"),
            moneda=llama_data.get("moneda", "PEN"),
            fecha_deposito=llama_data.get("fecha_operacion"),
            numero_operacion=llama_data.get("numero_operacion", ""),
            estado="procesado" # <- SIEMPRE procesado
        )
        
        # 11. Actualizar BD
        success = await _db.update_deposit(deposit_id, update_data)
        
        if not success:
            raise RuntimeError("Falló actualización BD")
        
        # PUBLICAR RESULTADO EN REDIS
        await _redis_queue.publish_result({
            "deposit_id": deposit_id,
            "status": "success",
            "error_type": None,
            "error_message": None,
        })

        logger.info("Resultado publicado en Redis", deposit_id=deposit_id)

        return {"status": "success"}
    finally:
        await _db.close()
