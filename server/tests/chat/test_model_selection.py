from __future__ import annotations

from typing import Any

import httpx
import pytest

from server.src.chat.model import (
    BifrostConfigurationError,
    BifrostModelFactory,
)
from server.src.chat.models import ConversationCreate, SendMessageRequest
from server.src.chat.repository import ChatRepository
from server.src.chat.runs import ChatRunRegistry
from server.src.chat.runtime import PreparedAgentRun
from server.src.chat.service import ChatService
from server.src.config import Settings
from server.src.guardrails.content import ContentGuardrailService
from server.src.guardrails.policy import PermissionPolicyStore
from server.src.memory.storage.sqlite import SQLiteDatabase
from server.src.skills.evaluation.fixtures import ReplayFixtureStore
from server.src.skills.trajectory_store import TrajectoryStore

DEFAULT_MODEL = "9router/claude-opus-free"
OLLAMA_CATALOG = ["ollama/qwen3:8b", "ollama/gemma3:12b"]


def _settings() -> Settings:
    return Settings.model_validate(
        {"chat": {"default_model": DEFAULT_MODEL}}
    )


def _enable_gateway(monkeypatch) -> None:
    """Point the factory at a fake gateway and drop any real override."""
    monkeypatch.delenv("TRAJECTA_BIFROST_URL", raising=False)
    monkeypatch.setenv("BIFROST_URL", "http://bifrost.test")


def _patch_v1_models(monkeypatch, handler) -> None:
    real_client = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)


def _catalog(catalog: dict[str, list[str]]):
    """Serve /v1/models scoped to whatever provider the caller asked for."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/models"
        provider = request.url.params.get("provider", "")
        return httpx.Response(
            200,
            json={
                "data": [
                    {"id": model}
                    for model in catalog.get(provider, [])
                ]
            },
        )

    return handler


async def test_selected_ollama_model_does_not_fall_back(
    monkeypatch,
) -> None:
    _enable_gateway(monkeypatch)
    _patch_v1_models(monkeypatch, _catalog({"ollama": OLLAMA_CATALOG}))
    factory = BifrostModelFactory(_settings())

    resolved = await factory.resolve_or_default("ollama/qwen3:8b")

    # The default provider must never swallow the selection.
    assert resolved == "ollama/qwen3:8b"
    assert resolved != DEFAULT_MODEL


async def test_unknown_selected_model_raises_instead_of_falling_back(
    monkeypatch,
) -> None:
    _enable_gateway(monkeypatch)
    _patch_v1_models(monkeypatch, _catalog({"ollama": OLLAMA_CATALOG}))
    factory = BifrostModelFactory(_settings())

    with pytest.raises(BifrostConfigurationError) as excinfo:
        await factory.resolve_or_default("ollama/does-not-exist")

    assert "ollama/does-not-exist" in str(excinfo.value)
    assert DEFAULT_MODEL not in str(excinfo.value)


async def test_bare_ids_belong_to_the_queried_provider(
    monkeypatch,
) -> None:
    """A bare catalog id is attributed to the provider asked for."""
    _enable_gateway(monkeypatch)
    queried: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        queried.append(request.url.params.get("provider", ""))
        return httpx.Response(
            200,
            json={"data": [{"id": "qwen3:8b"}]},
        )

    _patch_v1_models(monkeypatch, handler)
    factory = BifrostModelFactory(_settings())

    resolved = await factory.resolve_or_default("ollama/qwen3:8b")

    assert queried == ["ollama"]
    # Not 9router/qwen3:8b — chat.default_model must not leak in.
    assert resolved == "ollama/qwen3:8b"


async def test_discovery_outage_does_not_block_the_selection(
    monkeypatch,
) -> None:
    _enable_gateway(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    _patch_v1_models(monkeypatch, handler)
    factory = BifrostModelFactory(_settings())

    resolved = await factory.resolve_or_default("ollama/qwen3:8b")

    assert resolved == "ollama/qwen3:8b"


async def test_without_a_gateway_the_selection_is_passed_through(
    monkeypatch,
) -> None:
    monkeypatch.delenv("TRAJECTA_BIFROST_URL", raising=False)
    monkeypatch.delenv("BIFROST_URL", raising=False)
    factory = BifrostModelFactory(_settings())

    resolved = await factory.resolve_or_default("ollama/qwen3:8b")

    assert resolved == "ollama/qwen3:8b"


class DummyAttachments:
    async def save(self, **_: Any) -> Any:
        raise AssertionError("not used")


class DummyRag:
    pass


class ResolvingRuntime:
    """Resolves the model the way AgentRuntime.prepare does."""

    def __init__(self, factory: BifrostModelFactory) -> None:
        self._factory = factory

    async def prepare(
        self,
        *,
        conversation,
        thread_id,
        model_name,
        base_checkpoint_id,
        task_text="",
    ):  # noqa: ANN001
        resolved = await self._factory.resolve_or_default(
            model_name or conversation.model
        )
        return PreparedAgentRun(
            agent=None,
            config={"configurable": {"thread_id": thread_id}},
            model_name=resolved,
            mcp_tool_count=0,
            thread_id=thread_id,
        )

    async def stream_prepared(self, **_: Any):  # noqa: ANN001
        raise AssertionError("streaming is not part of this test")


async def test_sending_a_message_keeps_the_selected_ollama_model(
    tmp_path,
    monkeypatch,
) -> None:
    """Regression: a selection must never be rewritten to the default."""
    _enable_gateway(monkeypatch)
    _patch_v1_models(monkeypatch, _catalog({"ollama": OLLAMA_CATALOG}))

    db = SQLiteDatabase(tmp_path / "chat.db")
    await db.open()

    settings = Settings.model_validate(
        {
            "chat": {"default_model": DEFAULT_MODEL},
            "memory": {"db_path": str(tmp_path / "chat.db")},
        }
    )
    service = ChatService(
        settings,
        ChatRepository(db),
        DummyAttachments(),  # type: ignore[arg-type]
        DummyRag(),  # type: ignore[arg-type]
        ResolvingRuntime(BifrostModelFactory(settings)),  # type: ignore[arg-type]
        ChatRunRegistry(),
        TrajectoryStore(db),
        ReplayFixtureStore(settings, db),
        ContentGuardrailService(settings, PermissionPolicyStore(db)),
    )

    conversation = await service.create_conversation(
        ConversationCreate(title="ollama chat", model="ollama/qwen3:8b")
    )
    assert conversation.model == "ollama/qwen3:8b"

    await service.prepare_message(
        conversation.id,
        SendMessageRequest(content="hello"),
    )

    stored = await service.get_conversation(conversation.id)
    assert stored.model == "ollama/qwen3:8b"
    assert stored.model != DEFAULT_MODEL


async def test_bare_model_id_is_not_prefixed_with_default_provider(monkeypatch) -> None:
    _enable_gateway(monkeypatch)
    _patch_v1_models(monkeypatch, _catalog({"": ["gateway-route"]}))
    factory = BifrostModelFactory(_settings())

    resolved = await factory.resolve_or_default("gateway-route")
    assert resolved == "gateway-route"
    assert factory.create("gateway-route").model_name == "gateway-route"


async def test_bare_routing_rule_alias_is_not_blocked_by_catalog(monkeypatch) -> None:
    _enable_gateway(monkeypatch)
    _patch_v1_models(monkeypatch, _catalog({"": ["openai/gpt-4o-mini"]}))
    factory = BifrostModelFactory(_settings())
    assert await factory.resolve_or_default("my-custom-route") == "my-custom-route"
