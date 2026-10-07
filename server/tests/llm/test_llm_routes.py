from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.src.api.routes.llm import router
from server.src.llm_gateway.bifrost_admin import BifrostAdminError


class FakeAdmin:
    """In-memory stand-in for BifrostAdminClient."""

    def __init__(self, *, healthy: bool = True) -> None:
        self.base_url = "http://127.0.0.1:8080"
        self.healthy = healthy
        self.providers: dict[str, dict] = {}
        self.keys: dict[str, list[dict]] = {}
        self.models: dict[str, list[str]] = {}
        self.upserts: list[dict] = []
        self.removed: list[str] = []
        self.tests: list[str] = []
        self.vk_flips = 0
        self.test_result: dict = {
            "reachable": True,
            "status": "success",
            "detail": "ok",
        }

    async def health(self) -> bool:
        return self.healthy

    async def list_providers(self) -> list[dict]:
        return list(self.providers.values())

    async def get_provider(self, provider: str) -> dict | None:
        return self.providers.get(provider)

    async def provider_keys(self, provider: str) -> list[dict]:
        return self.keys.get(provider, [])

    async def upsert_provider(
        self,
        *,
        provider: str,
        provider_type: str,
        base_url: str | None = None,
        extra_headers: dict[str, str] | None = None,
        api_key: str | None = None,
    ) -> None:
        self.upserts.append(
            {
                "provider": provider,
                "provider_type": provider_type,
                "base_url": base_url,
                "extra_headers": extra_headers,
                "api_key": api_key,
            }
        )
        self.providers[provider] = {
            "name": provider,
            "network_config": {"base_url": base_url or ""},
        }
        if api_key:
            self.keys[provider] = [
                {"id": "k1", "status": "success"}
            ]
        elif provider_type == "ollama":
            self.keys[provider] = [{"id": "k0", "status": ""}]

    async def remove_provider(self, provider: str) -> bool:
        self.removed.append(provider)
        return self.providers.pop(provider, None) is not None

    async def provider_models(self, provider: str) -> list[str]:
        return self.models.get(provider, [])

    async def test_provider(self, provider: str) -> dict:
        self.tests.append(provider)
        return dict(self.test_result)

    async def ensure_allow_all_providers(self) -> None:
        self.vk_flips += 1


class FakeStore:
    def __init__(self, runtime: dict[str, Any] | None = None) -> None:
        self.runtime = runtime or {
            "gateway": {"type": "bifrost"},
            "default_provider": "9router",
            "default_model": "9router/claude-opus-free",
        }
        self.saved: tuple[str, str] | None = None

    async def get(self) -> dict:
        return dict(self.runtime)

    async def set(
        self,
        *,
        default_provider: str,
        default_model: str,
    ) -> None:
        self.saved = (default_provider, default_model)
        self.runtime = {
            "gateway": {"type": "bifrost"},
            "default_provider": default_provider,
            "default_model": default_model,
        }


def _client(
    *,
    admin: FakeAdmin | None = None,
    store: FakeStore | None = None,
) -> tuple[TestClient, FakeAdmin, FakeStore]:
    admin = admin or FakeAdmin()
    store = store or FakeStore()

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.state.llm_admin = admin
    app.state.llm_settings = store

    return TestClient(app), admin, store


def _seed_9router(admin: FakeAdmin) -> None:
    admin.providers["9router"] = {
        "name": "9router",
        "network_config": {
            "base_url": "http://host.docker.internal:20128",
        },
    }
    admin.keys["9router"] = [{"id": "k9", "status": "success"}]


def test_catalog_reports_gateway_and_provider_entries() -> None:
    client, admin, _ = _client()
    _seed_9router(admin)

    payload = client.get("/api/v1/llm").json()

    assert payload["gateway"] == {
        "type": "bifrost",
        "url": "http://127.0.0.1:8080",
        "reachable": True,
    }
    assert payload["default_provider"] == "9router"
    assert payload["default_model"] == "9router/claude-opus-free"

    by_id = {entry["id"]: entry for entry in payload["providers"]}
    assert set(by_id) == {"9router", "openai", "anthropic", "ollama"}
    assert by_id["9router"] == {
        "id": "9router",
        "type": "openai_compat",
        "configured": True,
        "reachable": True,
    }
    assert by_id["openai"]["configured"] is False
    assert by_id["openai"]["reachable"] is False
    assert by_id["ollama"]["type"] == "ollama"
    assert by_id["anthropic"]["type"] == "anthropic"


def test_catalog_with_gateway_down_marks_everything_unreachable() -> None:
    client, admin, _ = _client(admin=FakeAdmin(healthy=False))
    _seed_9router(admin)

    payload = client.get("/api/v1/llm").json()

    assert payload["gateway"]["reachable"] is False
    for entry in payload["providers"]:
        assert entry["reachable"] is False


def test_configured_provider_without_working_key_is_not_reachable() -> None:
    client, admin, _ = _client()
    _seed_9router(admin)
    admin.keys["9router"] = [
        {"id": "k9", "status": "list_models_failed", "description": "down"}
    ]

    payload = client.get("/api/v1/llm").json()
    by_id = {entry["id"]: entry for entry in payload["providers"]}

    assert by_id["9router"]["configured"] is True
    assert by_id["9router"]["reachable"] is False


def test_upsert_custom_provider_forwards_to_bifrost_and_flips_vk() -> None:
    client, admin, _ = _client()

    response = client.put(
        "/api/v1/llm/providers/myrelay",
        json={
            "type": "openai_compat",
            "base_url": "http://localhost:9000",
            "api_key": "sk-test",
            "extra_headers": {"X-Tenant": "acme"},
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "id": "myrelay",
        "type": "openai_compat",
        "configured": True,
        "reachable": True,
    }
    assert admin.upserts == [
        {
            "provider": "myrelay",
            "provider_type": "openai_compat",
            "base_url": "http://localhost:9000",
            "extra_headers": {"X-Tenant": "acme"},
            "api_key": "sk-test",
        }
    ]
    # The Trajecta virtual key must accept the new provider.
    assert admin.vk_flips == 1


def test_upsert_discovers_models_without_pressing_test() -> None:
    """Saving a provider refreshes Bifrost's models, so Chat sees them."""
    client, admin, _ = _client()

    response = client.put(
        "/api/v1/llm/providers/myrelay",
        json={
            "type": "openai_compat",
            "base_url": "http://localhost:9000",
            "api_key": "sk-test",
        },
    )

    assert response.status_code == 200
    assert admin.tests == ["myrelay"]


def test_upsert_saves_even_when_discovery_is_down() -> None:
    """Discovery is best-effort — an unreachable upstream still saves."""

    class FailingAdmin(FakeAdmin):
        async def test_provider(self, provider: str) -> dict:
            raise BifrostAdminError("upstream is down")

    admin = FailingAdmin()
    client, _, _ = _client(admin=admin)

    response = client.put(
        "/api/v1/llm/providers/myrelay",
        json={
            "type": "openai_compat",
            "base_url": "http://localhost:9000",
            "api_key": "sk-test",
        },
    )

    assert response.status_code == 200
    assert response.json()["configured"] is True
    assert admin.upserts and admin.vk_flips == 1


def test_ollama_defaults_to_the_docker_safe_base_url() -> None:
    client, admin, _ = _client()

    response = client.put(
        "/api/v1/llm/providers/ollama",
        json={"type": "ollama"},
    )

    assert response.status_code == 200
    # Bifrost runs in Docker, where localhost is the container itself.
    assert admin.upserts[0]["base_url"] == (
        "http://host.docker.internal:11434"
    )
    assert admin.upserts[0]["api_key"] is None
    assert admin.vk_flips == 1


def test_native_type_must_match_the_provider_id() -> None:
    client, _, _ = _client()

    response = client.put(
        "/api/v1/llm/providers/gpt-4o",
        json={"type": "openai", "api_key": "sk-x"},
    )

    assert response.status_code == 422
    assert "openai" in response.json()["detail"]


def test_custom_provider_cannot_shadow_a_builtin_id() -> None:
    client, _, _ = _client()

    response = client.put(
        "/api/v1/llm/providers/ollama",
        json={
            "type": "openai_compat",
            "base_url": "http://localhost:11434",
        },
    )

    assert response.status_code == 422
    assert "built-in" in response.json()["detail"]


def test_openai_compat_requires_a_base_url() -> None:
    client, _, _ = _client()

    response = client.put(
        "/api/v1/llm/providers/myrelay",
        json={"type": "openai_compat"},
    )

    assert response.status_code == 422
    assert "base_url" in response.json()["detail"]


def test_delete_removes_a_provider_and_404s_when_missing() -> None:
    client, admin, _ = _client()
    _seed_9router(admin)

    response = client.delete("/api/v1/llm/providers/9router")
    assert response.status_code == 200
    assert response.json() == {"deleted": "9router"}
    assert admin.removed == ["9router"]
    assert "9router" not in admin.providers

    missing = client.delete("/api/v1/llm/providers/9router")
    assert missing.status_code == 404


def test_models_endpoint_lists_known_models() -> None:
    client, admin, _ = _client()
    _seed_9router(admin)
    admin.models["9router"] = ["9router/af/gpt-oss-120b"]

    payload = client.get(
        "/api/v1/llm/providers/9router/models"
    ).json()
    assert payload == {"models": ["9router/af/gpt-oss-120b"]}

    missing = client.get("/api/v1/llm/providers/nope/models")
    assert missing.status_code == 404


def test_test_endpoint_proxies_the_gateway_result() -> None:
    client, admin, _ = _client()
    _seed_9router(admin)
    admin.test_result = {
        "reachable": False,
        "status": "failed",
        "detail": "list_models_failed",
    }

    payload = client.post(
        "/api/v1/llm/providers/9router/test"
    ).json()

    assert payload == admin.test_result
    assert admin.tests == ["9router"]

    missing = client.post("/api/v1/llm/providers/nope/test")
    assert missing.status_code == 404


def test_put_default_persists_and_repairs_a_bare_model() -> None:
    client, _, store = _client()

    response = client.put(
        "/api/v1/llm/default",
        json={
            "default_provider": "ollama",
            "default_model": "llama3.2",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "default_provider": "ollama",
        "default_model": "ollama/llama3.2",
    }
    assert store.saved == ("ollama", "ollama/llama3.2")


def test_put_default_rejects_a_mismatched_provider_prefix() -> None:
    client, _, store = _client()

    response = client.put(
        "/api/v1/llm/default",
        json={
            "default_provider": "openai",
            "default_model": "ollama/llama3.2",
        },
    )

    assert response.status_code == 422
    assert store.saved is None


def test_put_default_requires_both_fields() -> None:
    client, _, store = _client()

    response = client.put(
        "/api/v1/llm/default",
        json={"default_provider": "", "default_model": ""},
    )

    assert response.status_code == 422
    assert store.saved is None
