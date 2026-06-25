from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
from fastapi.responses import Response

# Contadores
quality_validations_total = Counter(
    "worker_quality_validations_total",
    "Total validaciones de calidad",
    ["result", "file_type"] # result: valid/invalid, file_type: image/pdf
)

quality_duration_seconds = Histogram(
    "worker_quality_duration_seconds",
    "Latencia validación calidad",
    ["file_type"]
)

llama_parser_calls_total = Counter(
    "worker_llama_parser_calls_total",
    "Total llamadas a LlamaParser",
    ["status"] # success, error, timeout
)

llama_parser_duration_seconds = Histogram(
    "worker_llama_parser_duration_seconds",
    "Latencia llamada LlamaParser"
)

deposit_processing_total = Counter(
    "worker_deposit_processing_total",
    "Total depósitos procesados",
    ["status"] # confirmado, rechazado, calidad_rechazado, error
)

deposit_processing_duration_seconds = Histogram(
    "worker_deposit_processing_duration_seconds",
    "Latencia procesamiento completo depósito"
)

db_updates_total = Counter(
    "worker_db_updates_total",
    "Total updates a BD",
    ["status"] # success. error
)

callback_notifications_total = Counter(
    "worker_callback_notifications_total",
    "Total callbacks a API Bridge",
    ["status"] # success, error
)

# Gauges
active_tasks = Gauge("worker_active_tasks", "Tasks Celery activos")

def metrics_endpoint():
    """Endpoint /metrics para Prometheus"""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)