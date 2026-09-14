"""FastAPI application factory for Trajecta.

All routes are prefixed /api/v1. Streaming endpoints use SSE.
"""

from __future__ import annotations

from fastapi import FastAPI

from server.src.config import Settings
from server.src.api.routes import all_routers


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build and configure the FastAPI application."""
    settings = settings or Settings.load()

    app = FastAPI(title="Trajecta", version="0.1.0")

    @app.get("/api/v1/health", tags=["health"], summary="Health check")
    def health() -> dict[str, str]:
        """Readiness probe — returns server name and version."""
        return {"status": "ok", "service": "trajecta", "version": app.version}

    # Register all API routers under /api/v1
    for router in all_routers:
        app.include_router(router, prefix="/api/v1")

    return app


app = create_app()