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
from server.src.chat.mcp import MCPToolProvider
from server.src.chat.mcp_settings import MCPToolSettingsStore
from server.src.chat.models_catalog import ModelCatalogService
from server.src.chat.models import ConversationCreate, SendMessageRequest
from server.src.config import Settings
from server.src.guardrails.content import (
    ContentGuardrailService,
)
from server.src.guardrails.policy import PermissionPolicyStore
from server.src.memory.provider import MemoryProvider, get_memory_provider
from server.src.skills.service import build_skills_service
from server.src.tools.personal import PersonalToolProvider
from server.src.tools.personal.scheduler import SchedulerService
from server.src.tools.verification import ConnectorVerificationService


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build and configure the FastAPI application."""
    settings = settings or Settings.load()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Open the shared memory provider (SQLite + FTS + vector + checkpointer + store).
        memory = get_memory_provider(settings)
        await memory.open()

        personal_tools = PersonalToolProvider(settings, memory)

        # One shared connector-verification service so chat and the receipt API
        # read/write the same receipts.
        connector_verification = ConnectorVerificationService(settings, memory.sqlite)

        # One shared MCP provider so the settings UI and chat read/write the
        # same tool preferences.
        mcp_preferences = MCPToolSettingsStore(memory.sqlite)
        mcp_tools = MCPToolProvider(
            settings,
            mcp_preferences,
            verification=connector_verification,
        )

        if memory.sqlite is None:
            raise RuntimeError("Memory store is not ready (SQLite unavailable)")

        permission_policy = PermissionPolicyStore(memory.sqlite)

        content_guardrails = ContentGuardrailService(
            settings,
            permission_policy,
        )

        # Skills must be built before ChatService: chat and learning use the
        # same trajectory store and replay-fixture store.
        skills = build_skills_service(
            settings,
            memory,
            personal_tools,
        )

        chat = build_chat_service(
            settings,
            memory,
            personal_tools,
            connector_verification,
            mcp_tools,
            permission_policy,
            content_guardrails=content_guardrails,
            trajectories=skills.trajectories,
            replay_fixtures=skills.replay_fixtures,
            skills=skills,
        )

        async def run_scheduled_job(job: dict) -> str:
            conversation_id = job.get("conversation_id")
            if not conversation_id:
                conversation = await chat.create_conversation(
                    ConversationCreate(title=f"Scheduled: {job.get('name', 'Trajecta task')}")
                )
                conversation_id = conversation.id
                await personal_tools.store.bind_schedule_conversation(str(job["id"]), conversation_id)
            turn = await chat.prepare_message(
                str(conversation_id),
                SendMessageRequest(content=str(job["prompt"])),
            )
            final_text = ""
            async for event in chat.stream_prepared(turn):
                if event.type == "message.completed":
                    message = event.data.get("message") or {}
                    final_text = str(message.get("content") or "")
                elif event.type == "run.error":
                    final_text = f"ERROR: {event.data.get('error') or event.data.get('message') or event.data}"
                elif event.type == "run.interrupted":
                    final_text = "PAUSED: This scheduled task requires user approval before it can continue."
            return final_text or "Scheduled run completed without a text response."

        scheduler = SchedulerService(
            personal_tools.store,
            run_scheduled_job,
            poll_seconds=settings.tools.scheduler.poll_seconds,
        )
        if settings.tools.scheduler.enabled:
            scheduler.start()

        app.state.settings = settings
        app.state.memory_provider = memory
        app.state.permission_policy = permission_policy
        app.state.content_guardrails = content_guardrails
        app.state.personal_tools = personal_tools
        app.state.connector_verification = connector_verification
        app.state.mcp_tools = mcp_tools
        app.state.chat_service = chat
        app.state.skills_service = skills
        app.state.scheduler = scheduler
        app.state.model_catalog = ModelCatalogService(settings)

        # Starts a lightweight worker. No mining happens unless the success
        # threshold is reached.
        skills.learning.start()

        try:
            yield

        finally:
            # Stop producers before SQLite/tools disappear.
            await scheduler.stop()
            await skills.learning.stop()
            await personal_tools.close()
            await memory.close()

    app = FastAPI(title="Trajecta", version="0.1.0", lifespan=lifespan)

    # Tauri desktop origins.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "tauri://localhost",
            "http://tauri.localhost",
            "http://localhost:1420",
            "http://127.0.0.1:1420",
            "tauri://default",
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