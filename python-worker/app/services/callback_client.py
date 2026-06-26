import httpx
from uuid import UUID
from typing import Optional, Dict, Any
from datetime import datetime
from app.config import get_settings
from app.models.deposit import DepositUpdateData
import structlog

logger = structlog.get_logger()

class CallbackClient:
    def __init__(self):
        self.settings = get_settings()
        self.base_url = self.settings.API_BRIDGE_URL.rstrip("/")
        self.internal_secret = self.settings.INTERNAL_SECRET

    async def notify_completion(self, result: Dict[str, Any]):
        """Notifica finalización a API Bridge"""
        payload = {
            "deposit_id": result["deposit_id"],
            "status": result["status"],
            "error_ids": result.get("error_ids", []),
            "warning_ids": result.get("warning_ids", []),
            "error_type": result.get("error_type"),
            "error_message": result.get("error_message"),
            "timestamp": datetime.utcnow().isoformat
        }

        headers = {
            "Content-Type": "application/json",
            "X-Internal-Secret": self.internal_secret
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    f"{self.base_url}/internal/webhooks/deposit-processed",
                    json=payload,
                    headers=headers
                )
                response.raise_for_status()

        except:
            raise