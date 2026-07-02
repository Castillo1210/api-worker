import structlog

from app.api.routes import health, process, quality

logger = structlog.get_logger()

def setup_routes(app):
    app.include_router(quality.router)
    app.include_router(health.router)
    app.include_router(process.router)
