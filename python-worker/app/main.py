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
    try:
        print(">>> [CONSUMER] Iniciando el loop de consumo de Redis...", flush=True)
        client = RedisQueueClient(redis_url=settings.REDIS_URL)

        print(f">>> [CONSUMER] Intentando conectar a {settings.REDIS_URL}...", flush=True)
        await client.connect()
        print(">>> [CONSUMER] Conexión exitosa. Esperando mensajes...", flush=True)

        while True:
            try:
                messages = await client.consume_process(count=5, block_ms=5000)
                for msg in messages:
                    deposit_id = msg.get("deposit_id") or msg.get("data")
                    banco_id = msg.get("banco_id")
                    msg_id = msg.get("_msg_id")
                    try:
                        if deposit_id:
                            if isinstance(deposit_id, str) and deposit_id.startswith("{"):
                                import json
                                data = json.loads(deposit_id)
                                deposit_id = data.get("deposit_id")
                                banco_id = data.get("banco_id") or banco_id
                            if deposit_id:
                                print(f">>> [CONSUMER] ¡Mensaje recibido! Encolando a Celery...", flush=True)
                                process_deposit.delay(str(deposit_id), banco_id)
                        await client.ack_process([msg_id])
                    except Exception as e:
                        print(f">>> [CONSUMER] Error encolando mensaje {msg_id}: {e}", flush=True)
                        await client.move_to_dlq(msg, str(e))
                        await client.ack_process([msg_id])
            except Exception as e:
                print(f">>> [CONSUMER] Error dentro del loop: {str(e)}", flush=True)
                await asyncio.sleep(5)
    except Exception as e:
        print(f">>> [CONSUMER] ERROR FATAL, EL CONSUMIDOR MURIÓ: {str(e)}", flush=True)

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
