from fastapi import APIRouter, File, UploadFile, HTTPException, Depends, Form
from typing import Optional
import time
import structlog

from app.models.deposit import QualityValidateResponse, QualityResult
from app.services.quality_validator import QualityValidator
from app.api.dependencies import get_quality_validator, get_metrics

logger = structlog.get_logger()

router = APIRouter(prefix="/validate-quality", tags=["Quality"])


def _build_validation_details(
    result: QualityResult,
    validator: QualityValidator,
    *,
    capture_mode: str,
    file_type: str,
    file_name: Optional[str],
    content_type: Optional[str],
    file_size: int,
    duration: float,
) -> dict:
    settings = validator.settings

    capture_type = result.capture_type or "unknown"
    capture_scores = result.capture_scores or {}
    metrics = result.metrics or {}

    if capture_type == "screenshot":
        applied_thresholds = {
            "min_width": min(settings.QUALITY_MIN_SCREENSHOT_WIDTH, 280),
            "min_height": min(settings.QUALITY_MIN_SCREENSHOT_HEIGHT, 500),
            "min_blur_score": settings.QUALITY_MIN_SCREENSHOT_BLUR_SCORE,
            "min_contrast": None,
            "max_glare_ratio": None,
            "min_document_confidence": None,
        }
    elif capture_type == "photo_of_screen":
        applied_thresholds = {
            "min_width": 320,
            "min_height": 480,
            "min_blur_score": settings.QUALITY_MIN_SCREEN_PHOTO_BLUR_SCORE,
            "min_contrast": settings.QUALITY_MIN_CONTRAST * 0.8,
            "max_glare_ratio": settings.QUALITY_MAX_GLARE_RATIO * 1.5,
            "min_document_confidence": None,
        }
    else:
        applied_thresholds = {
            "min_width": settings.QUALITY_MIN_WIDTH,
            "min_height": settings.QUALITY_MIN_HEIGHT,
            "min_blur_score": settings.QUALITY_MIN_BLUR_SCORE,
            "min_contrast": settings.QUALITY_MIN_CONTRAST,
            "max_glare_ratio": settings.QUALITY_MAX_GLARE_RATIO,
            "min_document_confidence": settings.QUALITY_MIN_DOC_CONFIDENCE,
        }

    checks = {
        "resolution": {
            "passed": "RESOLUTION_TOO_LOW" not in result.issues,
            "width": metrics.get("width"),
            "height": metrics.get("height"),
        },
        "blur": {
            "passed": "BLURRY" not in result.issues,
            "score": metrics.get("blur_score"),
            "threshold": applied_thresholds["min_blur_score"],
        },
        "contrast": {
            "passed": "LOW_CONTRAST" not in result.issues,
            "score": metrics.get("contrast"),
            "threshold": applied_thresholds["min_contrast"],
        },
        "glare": {
            "passed": "GLARE_DETECTED" not in result.issues,
            "score": metrics.get("glare_ratio"),
            "threshold": applied_thresholds["max_glare_ratio"],
        },
        "document": {
            "passed": "NO_DOCUMENT_DETECTED" not in result.issues,
            "score": metrics.get("document_confidence"),
            "threshold": applied_thresholds["min_document_confidence"],
        },
    }

    return {
        "input": {
            "file_name": file_name,
            "content_type": content_type,
            "file_type": file_type,
            "file_size_bytes": file_size,
            "capture_mode_received": capture_mode,
        },
        "decision": {
            "valid": result.is_valid,
            "capture_type": capture_type,
            "issues": list(result.issues),
            "processed": bool(result.processed_bytes),
        },
        "scores": {
            "capture_scores": capture_scores,
            "metrics": metrics,
        },
        "thresholds_applied": applied_thresholds,
        "checks": checks,
        "timing": {
            "duration_seconds": duration,
        },
    }


def _capture_label(capture_type: Optional[str]) -> dict:
    labels = {
        "photo": {
            "capture_label": "Foto",
            "capture_description": "Imagen tomada con cámara de un voucher, papel o comprobante físico.",
        },
        "photo_of_screen": {
            "capture_label": "Foto de pantalla",
            "capture_description": "Imagen tomada con cámara a una pantalla de celular, tablet o monitor.",
        },
        "screenshot": {
            "capture_label": "Screenshot",
            "capture_description": "Captura digital tomada desde el sistema sin usar cámara.",
        },
    }

    return labels.get(
        capture_type or "",
        {
            "capture_label": "Desconocido",
            "capture_description": "No fue posible clasificar el tipo de imagen con suficiente certeza.",
        },
    )


def _build_classification(result: QualityResult, capture_mode: str) -> dict:
    capture_meta = _capture_label(result.capture_type)
    capture_scores = result.capture_scores or {}

    return {
        "capture_type": result.capture_type or "unknown",
        "capture_label": capture_meta["capture_label"],
        "capture_description": capture_meta["capture_description"],
        "capture_mode_received": capture_mode,
        "scores": capture_scores,
    }

@router.post("", response_model=QualityValidateResponse)
async def validate_quality(
    file: UploadFile = File(...),
    capture_mode: str = Form("auto"),
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
        result = validator.validate(file_bytes, file_type, capture_mode=capture_mode)

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
            classification = _build_classification(result, capture_mode)

            return QualityValidateResponse(
                valid=True,
                should_proceed=True,
                next_action="continue",
                issues=[],
                message="Imagen/PDF válido para procesamiento",
                processed_file=processed_b64,
                file_type=file_type,
                capture_type=result.capture_type,
                capture_label=classification["capture_label"],
                capture_description=classification["capture_description"],
                capture_mode=capture_mode,
                classification=classification,
                validation_details=_build_validation_details(
                    result,
                    validator,
                    capture_mode=capture_mode,
                    file_type=file_type,
                    file_name=file.filename,
                    content_type=file.content_type,
                    file_size=len(file_bytes),
                    duration=duration,
                ),
            )
        else:
            metrics["quality_validations_total"].labels(result="invalid", file_type=file_type).inc()

            message = f"Archivo no cumple con requisitos de calidad: {', '.join(result.issues)}"

            classification = _build_classification(result, capture_mode)
            logger.warning(
                "Validación fallida",
                file_type=file_type,
                issues=result.issues,
                duration=duration,
                capture_type=classification["capture_type"],
                capture_label=classification["capture_label"],
            )

            return QualityValidateResponse(
                valid=False,
                should_proceed=False,
                next_action="retry_upload",
                issues=result.issues,
                message=message,
                processed_file=None,
                file_type=file_type,
                capture_type=result.capture_type,
                capture_label=classification["capture_label"],
                capture_description=classification["capture_description"],
                capture_mode=capture_mode,
                classification=classification,
                validation_details=_build_validation_details(
                    result,
                    validator,
                    capture_mode=capture_mode,
                    file_type=file_type,
                    file_name=file.filename,
                    content_type=file.content_type,
                    file_size=len(file_bytes),
                    duration=duration,
                ),
            )
    
    except Exception as e:
        metrics["quality_validations_total"].labels(result="error", file_type=file_type).inc()
        logger.error("Error en endpoint validación", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail="Error interno validando calidad")
