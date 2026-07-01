from fastapi import FastAPI
from contextlib import asynccontextmanager
from app.api import setup_routes
from app.api.dependencies import get_db_updater, get_storage_client
from app.config import get_settings
import structlog

logger = structlog.get_logger()
settings = get_settings()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Iniciando Confirmo Worker")

    db = None
    if settings.ENVIRONMENT != "development":
        # Inicializar conexiones externas solo fuera del modo local.
        db = await get_db_updater()
        get_storage_client()
        logger.info("Dependencias inicializadas")
    else:
        logger.info("Modo development: dependencias externas no inicializadas")

    yield

    # Shutdown
    logger.info("Cerrando Confirmo Worker")
    if db is not None:
        await db.close()
    logger.info("Confirmo Worker detenido")

app = FastAPI(
    title="Confirmo Worker API",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs" if settings.ENVIRONMENT != "production" else None,
    redoc_url=None
)

# Setup routes
setup_routes(app)

# Health check simple
@app.get("/health")
async def health():
    return {"status": "healthy", "service": "confirmo-worker"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8081)
