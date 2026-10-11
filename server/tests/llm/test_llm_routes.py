"""Trajecta must not administer providers: only gateway reads + model default."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.src.api.routes.llm import router


class FakeAdmin:
    def __init__(self, *, healthy: bool = True) -> None:
        self.base_url = "http://127.0.0.1:8080"
        self.healthy = healthy
        self.providers = [{"name": "ollama"}, {"name": "9router"}]
        self.keys = {
            "ollama": [{"status": ""}],
            "9router": [{"status": "success"}],
        }
        self.reads = 0

    async def health(self) -> bool:
        return self.healthy

    async def list_providers(self) -> list[dict]:
        self.reads += 1
        return self.providers

    async def provider_keys(self, provider: str) -> list[dict]:
        self.reads += 1
        return self.keys.get(provider, [])


class FakeStore:
    def __init__(self) -> None:
        self.runtime = {
            "gateway": {"type": "bifrost"},
            "default_provider": "9router",
            "default_model": "9router/claude-opus-free",
        }
        self.saved: tuple[str, str] | None = None

    async def get(self) -> dict:
        return dict(self.runtime)

    async def set(self, *, default_provider: str, default_model: str) -> None:
        self.saved = (default_provider, default_model)
        self.runtime.update(
            default_provider=default_provider,
            default_model=default_model,
        )


def _client(*, healthy: bool = True) -> tuple[TestClient, FakeAdmin, FakeStore]:
    admin = FakeAdmin(healthy=healthy)
    store = FakeStore()
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.state.llm_admin = admin
    app.state.llm_settings = store
    return TestClient(app), admin, store


def test_catalog_is_read_only_and_reports_configured_providers() -> None:
    client, admin, _ = _client()
    response = client.get("/api/v1/llm")
    assert response.status_code == 200
    data = response.json()
    assert data["gateway"] == {
        "type": "bifrost", "url": "http://127.0.0.1:8080", "reachable": True,
    }
    assert data["default_model"] == "9router/claude-opus-free"
    assert {entry["id"] for entry in data["providers"]} == {"ollama", "9router"}
    assert all(entry["configured"] for entry in data["providers"])
    assert admin.reads == 3


def test_gateway_offline_does_not_show_stale_providers() -> None:
    client, admin, _ = _client(healthy=False)
    result = client.get("/api/v1/llm").json()
    assert result["gateway"]["reachable"] is False
    assert result["providers"] == []
    assert admin.reads == 0


def test_provider_write_and_test_endpoints_are_not_exposed() -> None:
    client, _, store = _client()
    assert client.put(
        "/api/v1/llm/providers/ollama",
        json={"type": "ollama", "api_key": "secret"},
    ).status_code == 404
    assert client.delete("/api/v1/llm/providers/ollama").status_code == 404
    assert client.post("/api/v1/llm/providers/ollama/test").status_code == 404
    assert client.get("/api/v1/llm/providers/ollama/models").status_code == 404
    assert store.saved is None


def test_default_model_accepts_qualified_model_without_provider_field() -> None:
    client, _, store = _client()
    response = client.put(
        "/api/v1/llm/default",
        json={"default_model": "ollama/qwen3:8b"},
    )
    assert response.status_code == 200
    assert store.saved == ("ollama", "ollama/qwen3:8b")


def test_default_model_preserves_bifrost_managed_bare_id() -> None:
    client, _, store = _client()
    response = client.put(
        "/api/v1/llm/default",
        json={"default_model": "my-routing-alias"},
    )
    assert response.status_code == 200
    assert response.json() == {
        "default_provider": "", "default_model": "my-routing-alias",
    }
    assert store.saved == ("", "my-routing-alias")


def test_legacy_provider_field_is_still_accepted() -> None:
    client, _, store = _client()
    response = client.put(
        "/api/v1/llm/default",
        json={"default_provider": "ollama", "default_model": "qwen3:8b"},
    )
    assert response.status_code == 200
    assert store.saved == ("ollama", "ollama/qwen3:8b")


def test_reject_mismatched_provider_and_empty_model() -> None:
    client, _, store = _client()
    response = client.put(
        "/api/v1/llm/default",
        json={"default_provider": "openai", "default_model": "ollama/llama3.2"},
    )
    assert response.status_code == 422
    response = client.put("/api/v1/llm/default", json={"default_model": "  "})
    assert response.status_code == 422
    response = client.put("/api/v1/llm/default", json={"default_model": "bad alias"})
    assert response.status_code == 422
    assert store.saved is None
