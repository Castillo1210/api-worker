set -e

echo "==== Confirmo Worker ===="
echo "Environment: $ENVIRONMENT"

# Iniciar Celery Worker en background
echo "Starting Celery worker..."
celery -A app.worker worker \
    --loglevel=info \
    --concurrency=2 \
    --prefetch-multiplier=1 \
    --queues=deposit_processing \
    &

# Iniciar FastAPI (foreground)
echo "Starting FastAPI on port 8081..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8081