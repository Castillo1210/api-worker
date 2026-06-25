from fastapi import APIRouter, Depends
from app.services.cloudsql_client import CloudSQLClient
from app.services.storage_client import StorageClient
from app.services.metrics import metrics_endpoint
from app.api.dependencies import get_db_updater, get_storage_client
import structlog

logger = structlog.get_logger()

router = APIRouter(tags=["Health"])

@router.get("/health")
async def health_check():
    """Health check básico"""
    return {
        "status": "healthy",
        "service": "confirmo-worker",
        "version": "1.0.0"
    }

@router.get("/health/ready")
async def readiness_check(
    db: CloudSQLClient = Depends(get_db_updater),
    storage: StorageClient = Depends(get_storage_client)
):
    """Readiness check - verifica dependencias"""
    checks = {}

    # DB
    try:
        if db.pool and not db.pool._closed:
            async with db.pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
            checks["database"] = "ok"
        else:
            checks["database"] = "disconnected"
    except Exception as e:
        checks["database"] = f"error: {e}"

    # GCS
    try:
        # Solo verificar que bucket existe
        storage.client.get_bucket(storage.settings.GCS_BUCKET)
        checks["storage"] = "ok"
    except Exception as e:
        checks["storage"] = f"error: {e}"

    # Redis (vía Celery)
    try:
        from app.worker import celery_app
        celery_app.control.ping(timeout=2)
        checks["redis"] = "ok"
    except Exception as e:
        checks["redis"] = f"error: {e}"

    all_ok = all(v == "ok" for v in checks.values())

    return {
        "status": "ready" if all_ok else "not_ready",
        "checks": checks
    }

# Métricas Prometheus
router.get("/metrics")(metrics_endpoint)