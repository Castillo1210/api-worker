from fastapi import FastAPI
from contextlib import asynccontextmanager
import asyncio
from app.api import setup_routes
from app.api.dependencies import get_db_updater, get_storage_client
from app.utils.redis_queue_client import RedisQueueClient
from app.tasks.process_deposit import process_deposit
from app.config import get_settings
import structlog

logger = structlog.get_logger()
settings = get_settings()

async def consume_redis_loop():
    """Consume deposit from Redis Stream and dispatch to Celery"""
    client = RedisQueueClient(redis_url=settings.REDIS_URL)
    await client.connect()
    logger.info("Redis Stream consumer iniciado", queue="deposit:process:queue")

    while True:
        try:
            messages = await client.consume_process(count=5, block_ms=5000)
            for msg in messages:
                deposit_id = msg.get("deposit_id") or msg.get("data")
                if deposit_id:
                    if isinstance(deposit_id, str) and deposit_id.startswith("{"):
                        import json
                        data = json.loads(deposit_id)
                        deposit_id = data.get("deposit_id")
                    if deposit_id:
                        process_deposit.delay(str(deposit_id))
                        await client.ack_process([msg.get("_msg_id")])
        except Exception as e:
            logger.error("Error en consumer loop", error=str(e))
            await asyncio.sleep(5)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Iniciando Confirmo Worker")
    db = await get_db_updater()
    storage = get_storage_client()
    logger.info("Dependencias inicializadas")
    
    # Iniciar consumer de Redis Stream en background
    consumer_task = asyncio.create_task(consume_redis_loop())

    yield

    # Shutdown
    logger.info("Cerrando Confirmo Worker")
    consumer_task.cancel()
    try:
        await consumer_task
    except asyncio.CancelledError:
        pass
    
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
