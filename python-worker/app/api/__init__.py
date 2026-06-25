from app.api.routes import quality, process, health

def setup_routes(app):
    app.include_router(quality.router)
    app.include_router(process.router)
    app.include_router(health.router)