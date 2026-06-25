from fastapi import APIRouter, File, UploadFile, HTTPException, Depends
from typing import Optional
import time
import structlog

from app.models.deposit import QualityValidateResponse, QualityResult
from app.services.quality_validator import QualityValidator
from app.api.dependencies import get_quality_validator, get_metrics

logger = structlog.get_logger()

router = APIRouter(prefix="/validate-quality", tags=["Quality"])

@router.post("", response_model=QualityValidateResponse)
async def validate_quality(
    file: UploadFile = File(...),
    validator: QualityValidator = Depends(get_quality_validator),
    metrics: dict = Depends(get_metrics)
):
    """
    Endpoint síncrono para validación de calidad desde Mobile App.
    Recibe imagen/PDF -> Retorna validación + imagen procesada si válido.
    Timeout objetivo: < 10s
    """

    start_time = time.time()

    # Leer archivo
    file_bytes = await file.read()
    file_type = "pdf" if file.content_type == "application/pdf" else "image"

    logger.info(
        "Validación calidad iniciada",
        filename=file.filename,
        content_type=file.content_type,
        size=len(file_bytes),
        file_type=file_type
    )

    try:
        # Validar
        result = validator.validate(file_bytes, file_type)

        # Métricas
        duration = time.time() - start_time
        metrics["quality_duration_seconds"].labels(file_type=file_type).observe(duration)

        if result.is_valid:
            metrics["quality_validations_total"].labels(result="valid", file_type=file_type).inc()

            # Convertir imagen procesada a base64
            processed_b64 = None
            if result.processed_bytes:
                import base64
                processed_b64 = base64.b64encode(result.processed_bytes).decode()

            logger.info("Validación exitosa", file_type=file_type, duration=duration)

            return QualityValidateResponse(
                valid=True,
                issues=[],
                message="Imagen/PDF válido para procesamiento",
                processed_file=processed_b64,
                file_type=file_type
            )
        else:
            metrics["quality_validations_total"].labels(result="invalid", file_type=file_type).inc()

            message = f"Archivo no cumple con requisitos de calidad: {', '.join(result.issues)}"

            logger.warning("Validación fallida", file_type=file_type, issues=result.issues, duration=duration)

            return QualityValidateResponse(
                valid=False,
                issues=result.issues,
                message=message,
                processed_file=None,
                file_type=file_type
            )
    
    except Exception as e:
        metrics["quality_validations_total"].labels(result="error", file_type=file_type).inc()
        logger.error("Error en endpoint validación", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail="Error interno validando calidad")