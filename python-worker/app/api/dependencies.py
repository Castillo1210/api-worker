# app/api/dependencies.py
from functools import lru_cache
from fastapi import Depends
from app.services.quality_validator import QualityValidator
from app.services.llama_parser_client import LlamaParserClient
from app.services.prompt_selector import PromptSelector
from app.services.cloudsql_client import CloudSQLClient
from app.services.storage_client import StorageClient
from app.services.callback_client import CallbackClient
from app.services.business_validator import BusinessValidator
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
_llama_parser_client: LlamaParserClient = None
_prompt_selector: PromptSelector = None
_db_updater: CloudSQLClient = None
_storage_client: StorageClient = None
_callback_client: CallbackClient = None
_business_validator: BusinessValidator = None


def get_quality_validator() -> QualityValidator:
    global _quality_validator
    if _quality_validator is None:
        _quality_validator = QualityValidator()
    return _quality_validator


def get_llama_parser_client() -> LlamaParserClient:
    global _llama_parser_client
    if _llama_parser_client is None:
        _llama_parser_client = LlamaParserClient()
    return _llama_parser_client

async def get_db_updater() -> CloudSQLClient:
    global _db_updater
    if _db_updater is None:
        _db_updater = CloudSQLClient()
        await _db_updater.connect()
    return _db_updater


def get_storage_client() -> StorageClient:
    global _storage_client
    if _storage_client is None:
        _storage_client = StorageClient()
    return _storage_client


def get_callback_client() -> CallbackClient:
    global _callback_client
    if _callback_client is None:
        _callback_client = CallbackClient()
    return _callback_client

def get_business_validator(db: CloudSQLClient = Depends(get_db_updater)) -> BusinessValidator:
    global _business_validator
    if _business_validator is None:
        _business_validator = BusinessValidator(db)
    return _business_validator

async def get_prompt_selector(db: CloudSQLClient = Depends(get_db_updater)) -> PromptSelector:
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