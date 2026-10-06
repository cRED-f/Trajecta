from __future__ import annotations

from typing import Any

from server.src.chat.models import ConversationCreate
from server.src.chat.repository import ChatRepository
from server.src.chat.runs import ChatRunRegistry
from server.src.chat.service import ChatService
from server.src.config import Settings
from server.src.guardrails.content import ContentGuardrailService
from server.src.guardrails.policy import PermissionPolicyStore
from server.src.llm_gateway.settings import LLMSettingsStore
from server.src.memory.storage.sqlite import SQLiteDatabase
from server.src.skills.evaluation.fixtures import ReplayFixtureStore
from server.src.skills.trajectory_store import TrajectoryStore


class DummyAttachments:
    async def save(self, **_: Any) -> Any:
        raise AssertionError("not used")


class DummyRag:
    pass


class DummyRuntime:
    pass


def _service(
    db: SQLiteDatabase,
    settings: Settings,
    *,
    llm_settings: LLMSettingsStore | None = None,
) -> ChatService:
    return ChatService(
        settings,
        ChatRepository(db),
        DummyAttachments(),  # type: ignore[arg-type]
        DummyRag(),  # type: ignore[arg-type]
        DummyRuntime(),  # type: ignore[arg-type]
        ChatRunRegistry(),
        TrajectoryStore(db),
        ReplayFixtureStore(settings, db),
        ContentGuardrailService(settings, PermissionPolicyStore(db)),
        llm_settings=llm_settings,
    )


async def test_new_conversation_uses_the_persisted_default(
    tmp_path,
) -> None:
    db = SQLiteDatabase(tmp_path / "chat.db")
    await db.open()

    settings = Settings.model_validate(
        {
            "chat": {"default_model": "9router/claude-opus-free"},
            "memory": {"db_path": str(tmp_path / "chat.db")},
        }
    )
    llm_settings = LLMSettingsStore(
        db,
        bootstrap_model=settings.chat.default_model,
    )
    await llm_settings.set(
        default_provider="ollama",
        default_model="ollama/llama3.2",
    )

    service = _service(db, settings, llm_settings=llm_settings)

    conversation = await service.create_conversation(
        ConversationCreate(title="new chat")
    )

    # Global default applies to NEW conversations only.
    assert conversation.model == "ollama/llama3.2"


async def test_explicit_model_wins_over_the_global_default(
    tmp_path,
) -> None:
    db = SQLiteDatabase(tmp_path / "chat.db")
    await db.open()

    settings = Settings.model_validate(
        {
            "chat": {"default_model": "9router/claude-opus-free"},
            "memory": {"db_path": str(tmp_path / "chat.db")},
        }
    )
    llm_settings = LLMSettingsStore(
        db,
        bootstrap_model=settings.chat.default_model,
    )
    await llm_settings.set(
        default_provider="ollama",
        default_model="ollama/llama3.2",
    )

    service = _service(db, settings, llm_settings=llm_settings)

    conversation = await service.create_conversation(
        ConversationCreate(
            title="pinned model",
            model="openai/gpt-4o-mini",
        )
    )

    assert conversation.model == "openai/gpt-4o-mini"


async def test_without_a_store_the_bootstrap_model_is_used(
    tmp_path,
) -> None:
    db = SQLiteDatabase(tmp_path / "chat.db")
    await db.open()

    settings = Settings.model_validate(
        {
            "chat": {"default_model": "9router/claude-opus-free"},
            "memory": {"db_path": str(tmp_path / "chat.db")},
        }
    )

    service = _service(db, settings)

    conversation = await service.create_conversation(
        ConversationCreate(title="bootstrap chat")
    )

    assert conversation.model == "9router/claude-opus-free"
