"""FastAPI application factory for Trajecta.

All routes are prefixed /api/v1. Streaming endpoints use SSE.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request

from server.src.config import Settings
from server.src.api.routes import all_routers
from server.src.memory.provider import MemoryProvider, get_memory_provider


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build and configure the FastAPI application."""
    settings = settings or Settings.load()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Open the shared memory provider (SQLite + FTS + vector + checkpointer + store).
        provider = get_memory_provider(settings)
        await provider.open()
        app.state.memory_provider = provider
        try:
            yield
        finally:
            provider = getattr(app.state, "memory_provider", None)
            if provider is not None:
                await provider.close()
                delattr(app.state, "memory_provider")

    app = FastAPI(title="Trajecta", version="0.1.0", lifespan=lifespan)

    @app.get("/api/v1/health", tags=["health"], summary="Health check")
    def health() -> dict[str, str]:
        """Readiness probe — returns server name and version."""
        return {"status": "ok", "service": "trajecta", "version": app.version}

    # Register all API routers under /api/v1
    for router in all_routers:
        app.include_router(router, prefix="/api/v1")

    return app


app = create_app()