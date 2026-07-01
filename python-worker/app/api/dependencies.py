from fastapi import Depends
from app.services.quality_validator import QualityValidator
from app.services.metrics import (
    quality_validations_total,
    quality_duration_seconds,
    llama_parser_calls_total,
    llama_parser_duration_seconds,
    deposit_processing_total,
    deposit_processing_duration_seconds,
    db_updates_total,
    callback_notifications_total,
)
import structlog

logger = structlog.get_logger()

# Instancias singleton
_quality_validator: QualityValidator = None
_llama_parser_client = None
_prompt_selector = None
_db_updater = None
_storage_client = None
_callback_client = None
_business_validator = None


def get_quality_validator() -> QualityValidator:
    global _quality_validator
    if _quality_validator is None:
        _quality_validator = QualityValidator()
    return _quality_validator


def get_llama_parser_client():
    global _llama_parser_client
    if _llama_parser_client is None:
        from app.services.llama_parser_client import LlamaParserClient
        _llama_parser_client = LlamaParserClient()
    return _llama_parser_client

async def get_db_updater():
    global _db_updater
    if _db_updater is None:
        from app.services.cloudsql_client import CloudSQLClient
        _db_updater = CloudSQLClient()
        await _db_updater.connect()
    return _db_updater


def get_storage_client():
    global _storage_client
    if _storage_client is None:
        from app.services.storage_client import StorageClient
        _storage_client = StorageClient()
    return _storage_client


def get_callback_client():
    global _callback_client
    if _callback_client is None:
        from app.services.callback_client import CallbackClient
        _callback_client = CallbackClient()
    return _callback_client

def get_business_validator(db = Depends(get_db_updater)):
    global _business_validator
    if _business_validator is None:
        from app.services.business_validator import BusinessValidator
        _business_validator = BusinessValidator(db)
    return _business_validator

async def get_prompt_selector(db = Depends(get_db_updater)):
    from app.services.prompt_selector import PromptSelector
    return PromptSelector(db)

# Métricas
def get_metrics():
    return {
        "quality_validations_total": quality_validations_total,
        "quality_duration_seconds": quality_duration_seconds,
        "llama_parser_calls_total": llama_parser_calls_total,
        "llama_parser_duration_seconds": llama_parser_duration_seconds,
        "deposit_processing_total": deposit_processing_total,
        "deposit_processing_duration_seconds": deposit_processing_duration_seconds,
        "db_updates_total": db_updates_total,
        "callback_notifications_total": callback_notifications_total,
    }
