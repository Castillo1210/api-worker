from pydantic_settings import BaseSettings
from functools import lru_cache
from typing import Optional
import json

class Settings(BaseSettings):
    APP_NAME: str = "confirmo-worker"
    ENVIRONMENT: str = "development"
    LOG_LEVEL: str = "INFO"

    # Cloud SQL (PostgreSQL)
    DATABASE_HOST: str = "cloudsql-proxy"
    DATABASE_PORT: int = 5432
    DATABASE_NAME: str = "confirmo"
    DATABASE_USER: str = "confirmo-app"
    DATABASE_PASSWORD: str
    DATABASE_POOL_MIN: int = 2
    DATABASE_POOL_MAX: int = 10

    # GCS
    GCS_BUCKET: str = "confirmo-vouchers"
    GCS_CREDENTIALS_PATH: Optional[str] = None

    # IA LlamaParse
    LLAMA_PARSER_URL: str
    LLAMA_PARSER_API_KEY: str
    LLAMA_PARSER_TIMEOUT: float = 30.0
    LLAMA_PARSER_MAX_RETRIES: int = 3
    LLAMA_PARSER_RETRY_BACKOFF: float = 2.0

    # API Bridge (SignalR callback)
    API_BRIDGE_URL: str = "http://api-bridge:8080"
    INTERNAL_SECRET: str

    # Sistema financiero (Webhook)
    FINANCE_WEBHOOK_URL: str
    FINANCE_WEBHOOK_SECRET: str
    FINANCE_WEBHOOK_TIMEOUT: float = 10.0

    # Firebase (FCM)
    FIREBASE_CREDENTIALS_PATH: str = "/secrets/firebase-sa.json"

    # Celery / Redis
    CELERY_BROKER_URL: str = "redis://redis:6379/0"
    CELERY_RESULT_BACKEND: str = "redis://redis:6379/1"
    CELERY_TASK_SERIALIZER: str = "json"
    CELERY_RESULT_SERIALIZER: str = "json"
    CELERY_ACCEPT_CONTENT: str = ["json"]
    CELERY_TIMEZONE: str = "UTC"
    CELERY_WORKER_CONCURRENCY: int = 2
    CELERY_WORKER_PREFETCH_MULTIPLIER: int = 1
    CELERY_TASK_ACKS_LATE: bool = True
    CELERY_TASK_REJECT_ON_WORKER_LOST: bool = True

    # Validación Calidad (OpenCV heurísticas v1)
    QUALITY_MIN_WIDTH: int = 800
    QUALITY_MIN_HEIGHT: int = 600
    QUALITY_MIN_BLUR_SCORE: float = 100.0
    QUALITY_MAX_GLARE_RATIO: float = 0.15
    QUALITY_MIN_CONTRAST: float = 30.0
    QUALITY_MIN_DOC_CONFIDENCE: float = 0.7
    QUALITY_PDF_DPI: int = 200

    # Prompts
    PROMPTS_DIR: str = "prompts"

    # Métricas
    METRICS_PORT: int = 9090
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True

@lru_cache
def get_settings() -> Settings:
    return Settings()