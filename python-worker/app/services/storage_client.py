from google.cloud import storage
from app.config import get_settings
import structlog

logger = structlog.get_logger()

class StorageClient:
    def __init__(self):
        self.settings = get_settings()
        if self.settings.GCS_CREDENTIALS_PATH:
            self.client = storage.Client.from_service_account_json(self.settings.GCS_CREDENTIALS_PATH)
        else:
            self.client = storage.Client()
        self.bucket = self.client.bucket(self.settings.GCS_BUCKET)

    def download_voucher(self, object_name: str) -> bytes:
        """Descarga imagen de GCS como bytes"""
        blob = self.bucket.blob(object_name)
        if not blob.exists():
            raise FileNotFoundError(f"Objeto no encontrado en GCS: {object_name}")
        
        image_bytes = blob.download_as_bytes()
        logger.info("Imagen descargada de GCS", object_name=object_name, size=len(image_bytes))
        return image_bytes
    
    def get_content_type(self, object_name: str) -> str:
        """Obtiene content-type del objeto"""
        blob = self.bucket.blob(object_name)
        blob.reload()
        return blob.content_type or "application/octet-stream"