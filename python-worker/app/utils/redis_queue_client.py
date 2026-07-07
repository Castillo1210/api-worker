import redis.asyncio as redis
import json
import os
from typing import Optional, Dict, Any, List
from datetime import datetime
import structlog

logger = structlog.get_logger()

class RedisQueueClient:
    def __init__(self, redis_url: str = None):
        self.redis_url = redis_url or os.getenv("REDIS_URL", "redis://redis:6379/0")
        self.redis: Optional[redis.Redis] = None
        self.process_queue = "deposit:process:queue"
        self.result_queue = "deposit:result:queue"
        self.dlq = "deposit:dlq"
        self.consumer_group = "workers"
        self.consumer_name = f"worker-{os.getpid()}"

    async def connect(self):
        """Inicializa conexión y consumer group"""
        self.redis = redis.from_url(self.redis_url, decode_responses=True)

        # Crear consumer groups si no existen
        for stream in [self.process_queue, self.result_queue]:
            try:
                await self.redis.xgroup_create(stream, self.consumer_group, id="0", mkstream=True)
            except redis.ResponseError as e:
                if "BUSYGROUP" not in str(e):
                    raise

        logger.info("Redis Queue conectado", url=self.redis_url)

    async def close(self):
        if self.redis:
            await self.redis.close()

    # ==== PROCESS QUEUE (API Bridge -> Worker) ====

    async def publish_process(self, message: Dict[str, Any]):
        """API Bridge publica trabajo para el Worker"""
        if not self.redis:
            await self.connect()
        await self.redis.xadd(self.process_queue, message)
        logger.debug("Publicado en process queue", message=message)

    async def consume_process(self, count: int = 10, block_ms: int = 5000) -> List[Dict]:
        """Worker consume trabajos"""
        if not self.redis:
            await self.connect()

        streams = {self.process_queue: ">"}
        try:
            result = await self.redis.xreadgroup(
                self.consumer_group,
                self.consumer_name,
                streams,
                count=count,
                block=block_ms
            )

            messages = []
            for stream, messages_list in result:
                for msg_id, data in messages_list:
                    data["_msg_id"] = msg_id
                    messages.append(data)
            return messages
        except redis.ResponseError as e:
            if "NOGROUP" in str(e):
                # Recrear grupo si no existe
                await self.redis.xgroup_create(self.process_queue, self.consumer_group, id="0", mkstream=True)
                return []
            raise

    async def ack_process(self, message_ids: List[str]):
        """Confirma procesamiento"""
        if self.redis and message_ids:
            await self.redis.xack(self.process_queue, self.consumer_group, *message_ids)
        
    
    # ==== RESULT QUEUE (Worker -> API Bridge) ====
    async def publish_result(self, message: Dict[str, Any]):
        """Worker publica resultado"""
        if not self.redis:
            await self.connect()
        message["processed_at"] = datetime.utcnow().isoformat()
        clean_message = {
            key: (value if value is not None else "")
            for key, value in message.items()
        }

        if "deposit_id" in clean_message:
            clean_message["deposit_id"] = str(clean_message["deposit_id"])

        await self.redis.xadd(self.result_queue, clean_message)
        logger.info("Resultado publicado", deposit_id=message.get("deposit_id"), status=message.get("status"))

    # ==== DLQ ====

    async def move_to_dlq(self, message: Dict[str, Any], error: str):
        """Mueve a Dead Letter Queue"""
        if not self.redis:
            await self.connect()
        dlq_message = dict(message)
        dlq_message["_error"] = error
        dlq_message["_failed_at"] = datetime.utcnow().isoformat()
        await self.redis.xadd(self.dlq, dlq_message)
        logger.warning("Mensaje movido a DLQ", error=error)