import asyncio
import time

import structlog
from celery import shared_task
from uuid import UUID
from app.config import get_settings
from app.services.document_ai_client import DocumentAiClient, DocumentAiError
from app.models.deposit import DepositRow, DepositUpdateData, ValidationResult
from app.services.cloudsql_client import CloudSQLClient
from app.services.storage_client import StorageClient
from app.services.llama_parser_client import LlamaParserClient, LlamaParserError
from app.utils.redis_queue_client import RedisQueueClient
from app.services.schema_registry import SchemaRegistry
from app.services.numero_operacion_rules import normalize_numero_operacion
from app.services.metrics import (
    deposit_processing_total,
    deposit_processing_duration_seconds,
)
from app.services.vision_ocr_client import obtener_texto_ocr
from app.utils.ocr_cross_check import (
    extraer_fechas, extraer_monedas, extraer_montos, extraer_numero_tarjeta,
    fecha_es_plausible, resolver_campo, UMBRAL_CONFIANZA_ALTA,
)

logger = structlog.get_logger()

# Instancias globales (reutilizadas entre tasks)
_db: CloudSQLClient = None
_storage: StorageClient = None
_llama: LlamaParserClient = None
_redis_queue: RedisQueueClient = None
_docai: DocumentAiClient = None

_loop: asyncio.AbstractEventLoop = None


def _init_services():
    global _db, _storage, _llama, _redis_queue
    if _db is None:
        _db = CloudSQLClient()
        _storage = StorageClient()
        _llama = LlamaParserClient(SchemaRegistry(), _db)
        _redis_queue = RedisQueueClient()

def _get_worker_loop():
    global _loop
    if _loop is None or _loop.is_closed():
        _loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_loop)
    return _loop


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
def process_deposit(self, deposit_id: str, banco_id: str):
    """
    Procesa el depósito hasta obtener la extracción de LlamaCloud
    y publica el resultado en Redis para que otro consumidor continúe.
    """
    _init_services()

    start_time = time.time()
    loop = _get_worker_loop()

    try:
        result = loop.run_until_complete(_process_deposit_async(deposit_id, banco_id))

        duration = time.time() - start_time
        deposit_processing_duration_seconds.observe(duration)
        deposit_processing_total.labels(status=result["status"]).inc()

        return result
    except Exception:
        duration = time.time() - start_time
        deposit_processing_duration_seconds.observe(duration)
        deposit_processing_total.labels(status="error").inc()
        raise

async def _process_deposit_async(deposit_id: str, banco_id: str):
    """Extrae campos con IA, actualiza BD, publica en Redis.
    No decide estados de negocio. Siempre pone 'procesado' cualquiera fuera el resultado."""

    # 1. Conectar BD
    await _db.connect()

    deposit = await _db.get_deposit_for_processing(deposit_id)
    if not deposit or not deposit.get("imagen_voucher"):
        return {"status": "error", "error_type": "no_image"}
    
    logger.info("Depósito cargado", deposit_id=deposit_id)
    
    # 4. Descargar archivo de GCS
    imagen_voucher = deposit.get("imagen_voucher")
    file_bytes = _storage.download_voucher(imagen_voucher)
    content_type = _storage.get_content_type(imagen_voucher)
    file_type = content_type.split("/")[-1] if "/" in content_type else "jpeg"

    logger.info("Archivo descargado", deposit_id=deposit_id, file_type=file_type, size=len(file_bytes))

    try:
        # Extraer con schema dinámico (sin prompt)
        llama_data = await _llama.extract(file_bytes, file_type, banco_id=banco_id)
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
    
    numero_operacion = normalize_numero_operacion(banco_id, llama_data.numero_operacion or "")

    # --- Chequeo cruzado OCR (Vision) vs. IA (LlamaCloud) ---
    fecha_iso = llama_data.fecha_deposito
    datos_ocr = None
    monto_final, moneda_final, fecha_final = llama_data.monto, llama_data.moneda, fecha_iso


    _tiene_numero_tarjeta = hasattr(llama_data, "numero_tarjeta")
    numero_tarjeta_final = None
    if _tiene_numero_tarjeta:
        numero_tarjeta_final = "".join(
            c for c in str(getattr(llama_data, "numero_tarjeta", "") or "") if c.isdigit()
        )[-4:] or None

    try:
        es_pdf = file_type == "pdf"
        texto_ocr = await asyncio.to_thread(obtener_texto_ocr, file_bytes, es_pdf)

        conf = llama_data.field_confidences or {}
        r_monto = resolver_campo(
            llama_data.monto,
            extraer_montos(texto_ocr),
            lambda a, b: abs(float(a) - float(b)) < 0.01,
            conf.get("monto"),
        )
        r_moneda = resolver_campo(llama_data.moneda, extraer_monedas(texto_ocr), lambda a, b: a == b, conf.get("moneda"))
        r_fecha = resolver_campo(
            fecha_iso,
            extraer_fechas(texto_ocr),
            lambda a, b: str(a)[:10] == str(b)[:10],
            conf.get("fecha_deposito"),
        )

        fecha_verificada = r_fecha["valor_final"]
        if fecha_verificada not in (None, "") and not fecha_es_plausible(fecha_verificada):
            r_fecha = {
                "accion": "revision_manual", "valor_final": fecha_verificada, "candidatos": r_fecha["candidatos"],
                "motivo": f"Año implausible: se obtuvo {fecha_verificada!r}, distinto al año actual.",
            }

        monto_final, moneda_final, fecha_final = r_monto["valor_final"], r_moneda["valor_final"], r_fecha["valor_final"]

        r_tarjeta = None
        if _tiene_numero_tarjeta:
            r_tarjeta = resolver_campo(
                numero_tarjeta_final,
                extraer_numero_tarjeta(texto_ocr),
                lambda a, b: a == b,
                conf.get("numero_tarjeta"),
            )
            numero_tarjeta_final = r_tarjeta["valor_final"]

        datos_ocr = {
            "verificacion": {
                "monto": {"accion": r_monto["accion"], "motivo": r_monto["motivo"]},
                "moneda": {"accion": r_moneda["accion"], "motivo": r_moneda["motivo"]},
                "fecha_deposito": {"accion": r_fecha["accion"], "motivo": r_fecha["motivo"]},
                **({"numero_tarjeta": {"accion": r_tarjeta["accion"], "motivo": r_tarjeta["motivo"]}} if r_tarjeta else {}),
            },
            "llama_field_confidence": conf,
            "ocr_candidatos": {"monto": r_monto["candidatos"], "moneda": r_moneda["candidatos"], "fecha_deposito": r_fecha["candidatos"],
                                **({"numero_tarjeta": r_tarjeta["candidatos"]} if r_tarjeta else {}),
                            },
        }
        logger.info(
            "Chequeo OCR completado",
            deposit_id=deposit_id,
            monto_accion=r_monto["accion"],
            moneda_accion=r_moneda["accion"],
            fecha_accion=r_fecha["accion"],
            monto_candidatos=r_monto["candidatos"],
            moneda_candidatos=r_moneda["candidatos"],
            fecha_candidatos=r_fecha["candidatos"],
        )
    except Exception:
        # Si Vision falla, no bloqueamos el depósito -- seguimos solo con la IA
        # sin datos_ocr (el frontend no muestra ninguna alerta de color en ese caso)
        logger.exception("Chequeo OCR falló; se continúa solo con LlamaCloud", deposit_id=deposit_id)
    
    # 10. Preparar datos para actualización
    update_data = DepositUpdateData(
        monto=monto_final,
        moneda=moneda_final or "PEN",
        fecha_deposito=fecha_final,
        numero_operacion=numero_operacion,
        numero_tarjeta=numero_tarjeta_final, # NUEVO
        datos_ocr=datos_ocr,
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
