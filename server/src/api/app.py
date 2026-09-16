"""FastAPI application factory for Trajecta.

All routes are prefixed /api/v1. Streaming endpoints use SSE.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request

from fastapi.middleware.cors import (
    CORSMiddleware,
)

from server.src.api.routes import all_routers
from server.src.chat import build_chat_service
from server.src.config import Settings
from server.src.memory.provider import MemoryProvider, get_memory_provider


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build and configure the FastAPI application."""
    settings = settings or Settings.load()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Open the shared memory provider (SQLite + FTS + vector + checkpointer + store).
        memory = get_memory_provider(settings)
        await memory.open()

        chat = build_chat_service(
            settings,
            memory,
        )

        app.state.settings = settings
        app.state.memory_provider = memory
        app.state.chat_service = chat

        try:
            yield

        finally:
            await memory.close()

    app = FastAPI(title="Trajecta", version="0.1.0", lifespan=lifespan)

    # Tauri desktop origins.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "tauri://localhost",
            "http://tauri.localhost",
            "http://localhost:1420",
        ],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Register all API routers under /api/v1
    for router in all_routers:
        app.include_router(router, prefix="/api/v1")

    return app


app = create_app()