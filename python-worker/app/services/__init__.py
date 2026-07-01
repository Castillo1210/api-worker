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


def __getattr__(name):
    if name == "QualityValidator":
        from app.services.quality_validator import QualityValidator
        return QualityValidator
    if name == "LlamaParserClient":
        from app.services.llama_parser_client import LlamaParserClient
        return LlamaParserClient
    if name == "CloudSQLClient":
        from app.services.cloudsql_client import CloudSQLClient
        return CloudSQLClient
    if name == "StorageClient":
        from app.services.storage_client import StorageClient
        return StorageClient
    if name == "CallbackClient":
        from app.services.callback_client import CallbackClient
        return CallbackClient
    if name == "BusinessValidator":
        from app.services.business_validator import BusinessValidator
        return BusinessValidator
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "QualityValidator",
    "LlamaParserClient",
    "CloudSQLClient",
    "StorageClient",
    "CallbackClient",
    "BusinessValidator",
    "quality_validations_total",
    "quality_duration_seconds",
    "llama_parser_calls_total",
    "llama_parser_duration_seconds",
    "deposit_processing_total",
    "deposit_processing_duration_seconds",
    "db_updates_total",
    "callback_notifications_total",
]
