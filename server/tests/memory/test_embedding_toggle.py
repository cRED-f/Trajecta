"""Embedding on/off switch: off falls back to the built-in default embedder."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.src.api.routes import memory as memory_routes
from server.src.api.routes.memory import router


class FakeVector:
    def __init__(
        self,
        provider: str = "placeholder",
        model: str | None = None,
        size: int = 64,
    ) -> None:
        self.embedding_provider = provider
        self.embedding_model = model
        self.vector_size = size

    def configure_placeholder(self) -> None:
        self.embedding_provider = "placeholder"
        self.embedding_model = None
        self.vector_size = 64


class FakeMemory:
    def __init__(
        self,
        *,
        provider: str = "placeholder",
        model: str | None = None,
        size: int = 64,
    ) -> None:
        self.sqlite = object()
        self.ollama_embedding_base_url = "http://localhost:11434"
        self.vector = FakeVector(provider=provider, model=model, size=size)
        self.reconfigured: list[tuple[str | None, int]] = []

    async def reconfigure_embedding(
        self,
        *,
        model: str | None,
        dimensions: int = 0,
    ) -> dict[str, int]:
        self.reconfigured.append((model, dimensions))

        if model is None:
            self.vector.configure_placeholder()
        else:
            self.vector.embedding_provider = "ollama"
            self.vector.embedding_model = model
            self.vector.vector_size = dimensions

        return {"memories": 2, "attachment_chunks": 1}


class FakePolicy:
    def __init__(self, values: dict[str, Any] | None = None) -> None:
        self.values: dict[str, Any] = dict(values or {})

    async def get_setting(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)

    async def set_setting(self, key: str, value: Any) -> None:
        self.values[key] = value


class FakeCatalog:
    """Replaces the Ollama catalog so no local server is needed."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url

    async def list_models(self) -> tuple[bool, list[Any], str | None]:
        return True, [], None

    async def probe(self, model: str) -> int:
        return 1024


def _client(
    monkeypatch,
    *,
    memory: FakeMemory | None = None,
    policy: FakePolicy | None = None,
    vector_store_enabled: bool = True,
) -> tuple[TestClient, FakeMemory, FakePolicy]:
    monkeypatch.setattr(
        memory_routes,
        "OllamaEmbeddingCatalogService",
        FakeCatalog,
    )

    memory = memory or FakeMemory()
    policy = policy or FakePolicy()

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.state.memory_provider = memory
    app.state.permission_policy = policy
    app.state.settings = SimpleNamespace(
        memory=SimpleNamespace(
            vector_store=SimpleNamespace(enabled=vector_store_enabled)
        )
    )

    return TestClient(app), memory, policy


def test_no_configuration_starts_off_on_the_default_embedder(monkeypatch) -> None:
    client, _, _ = _client(monkeypatch)

    payload = client.get("/api/v1/memory/embedding").json()

    assert payload["enabled"] is False
    assert payload["provider"] == "placeholder"
    assert payload["selected_model"] is None
    assert payload["dimensions"] == 64


def test_turning_off_rebuilds_with_the_default_embedder(monkeypatch) -> None:
    memory = FakeMemory(provider="ollama", model="qwen3-embedding:0.6b", size=1024)
    policy = FakePolicy(
        {
            "memory.embedding": {
                "enabled": True,
                "provider": "ollama",
                "model": "qwen3-embedding:0.6b",
                "dimensions": 1024,
            }
        }
    )
    client, memory, policy = _client(monkeypatch, memory=memory, policy=policy)

    payload = client.patch(
        "/api/v1/memory/embedding",
        json={"enabled": False},
    ).json()

    assert payload["enabled"] is False
    assert payload["provider"] == "placeholder"
    assert payload["reindexed"] == {"memories": 2, "attachment_chunks": 1}
    assert memory.reconfigured == [(None, 0)]
    assert memory.vector.embedding_provider == "placeholder"
    assert memory.vector.vector_size == 64

    # The last model is remembered so switching back on restores it.
    assert policy.values["memory.embedding"] == {
        "enabled": False,
        "provider": "placeholder",
        "model": "qwen3-embedding:0.6b",
        "dimensions": 1024,
    }

    refreshed = client.get("/api/v1/memory/embedding").json()
    assert refreshed["enabled"] is False
    assert refreshed["selected_model"] == "qwen3-embedding:0.6b"


def test_turning_on_without_a_model_keeps_the_default(monkeypatch) -> None:
    client, memory, policy = _client(monkeypatch)

    payload = client.patch(
        "/api/v1/memory/embedding",
        json={"enabled": True},
    ).json()

    assert payload["enabled"] is True
    assert payload["provider"] == "placeholder"
    assert payload["reindexed"] == {"memories": 0, "attachment_chunks": 0}
    assert memory.reconfigured == []
    assert policy.values["memory.embedding"]["enabled"] is True


def test_turning_on_restores_the_remembered_model(monkeypatch) -> None:
    policy = FakePolicy(
        {
            "memory.embedding": {
                "enabled": False,
                "provider": "placeholder",
                "model": "qwen3-embedding:0.6b",
                "dimensions": 1024,
            }
        }
    )
    client, memory, policy = _client(monkeypatch, policy=policy)

    payload = client.patch(
        "/api/v1/memory/embedding",
        json={"enabled": True},
    ).json()

    assert payload["enabled"] is True
    assert payload["provider"] == "ollama"
    assert payload["selected_model"] == "qwen3-embedding:0.6b"
    assert payload["dimensions"] == 1024
    assert memory.reconfigured == [("qwen3-embedding:0.6b", 1024)]
    assert policy.values["memory.embedding"] == {
        "enabled": True,
        "provider": "ollama",
        "model": "qwen3-embedding:0.6b",
        "dimensions": 1024,
    }
