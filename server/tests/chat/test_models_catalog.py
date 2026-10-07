from __future__ import annotations

import httpx

from server.src.chat.models_catalog import ModelCatalogService
from server.src.config import Settings
from server.src.llm_gateway.bifrost_admin import BifrostAdminError


class FakeAdmin:
    """Stand-in for BifrostAdminClient covering the catalog reads."""

    def __init__(
        self,
        *,
        healthy: bool = True,
        providers: list[str] | None = None,
        models: dict[str, list[str]] | None = None,
        broken: list[str] | None = None,
    ) -> None:
        self.healthy = healthy
        self._providers = providers or []
        self._models = models or {}
        self._broken = set(broken or [])

    async def health(self) -> bool:
        return self.healthy

    async def list_providers(self) -> list[dict]:
        return [{"name": provider} for provider in self._providers]

    async def provider_models(self, provider: str) -> list[str]:
        if provider in self._broken:
            raise BifrostAdminError(f"provider {provider!r} failed")
        return self._models.get(provider, [])


def _no_gateway(monkeypatch) -> None:
    """Pin gateway_base_url() to None so /v1/models is never fetched."""
    monkeypatch.delenv("TRAJECTA_BIFROST_URL", raising=False)
    monkeypatch.delenv("BIFROST_URL", raising=False)


async def test_management_catalog_surfaces_dynamically_added_providers(
    monkeypatch,
) -> None:
    """A provider added from Settings shows up in the Chat catalog."""
    _no_gateway(monkeypatch)
    admin = FakeAdmin(
        providers=["ollama"],
        models={"ollama": ["ollama/qwen3:8b", "ollama/gemma3:12b"]},
    )

    catalog = await ModelCatalogService(
        Settings(),
        admin=admin,
    ).list_models()

    assert catalog.gateway_reachable is True
    assert {model.id for model in catalog.models} >= {
        "openai/gpt-4o-mini",
        "ollama/qwen3:8b",
        "ollama/gemma3:12b",
    }


async def test_without_an_admin_only_the_configured_model_is_listed(
    monkeypatch,
) -> None:
    """admin=None keeps the previous behaviour: no management catalog."""
    _no_gateway(monkeypatch)

    catalog = await ModelCatalogService(Settings()).list_models()

    assert catalog.gateway_reachable is False
    assert [model.id for model in catalog.models] == ["openai/gpt-4o-mini"]


async def test_one_failing_provider_does_not_hide_the_others(
    monkeypatch,
) -> None:
    _no_gateway(monkeypatch)
    admin = FakeAdmin(
        providers=["ollama", "ghost"],
        models={"ollama": ["ollama/qwen3:8b"]},
        broken=["ghost"],
    )

    catalog = await ModelCatalogService(
        Settings(),
        admin=admin,
    ).list_models()

    assert {model.id for model in catalog.models} >= {
        "ollama/qwen3:8b",
    }


async def test_management_catalog_is_skipped_when_the_gateway_is_down(
    monkeypatch,
) -> None:
    _no_gateway(monkeypatch)
    admin = FakeAdmin(
        healthy=False,
        providers=["ollama"],
        models={"ollama": ["ollama/qwen3:8b"]},
    )

    catalog = await ModelCatalogService(
        Settings(),
        admin=admin,
    ).list_models()

    assert catalog.gateway_reachable is False
    assert [model.id for model in catalog.models] == ["openai/gpt-4o-mini"]


async def test_union_with_the_v1_catalog_keeps_its_metadata(
    monkeypatch,
) -> None:
    """/v1/models wins on duplicates so owned_by and friends survive."""
    monkeypatch.delenv("TRAJECTA_BIFROST_URL", raising=False)
    monkeypatch.setenv("BIFROST_URL", "http://bifrost.test")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/models"
        return httpx.Response(
            200,
            json={
                "data": [
                    {"id": "ollama/qwen3:8b", "owned_by": "ollama"},
                ]
            },
        )

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda *args, **kwargs: real_client(
            *args,
            **{**kwargs, "transport": httpx.MockTransport(handler)},
        ),
    )

    admin = FakeAdmin(
        providers=["ollama"],
        models={"ollama": ["ollama/qwen3:8b", "ollama/gemma3:12b"]},
    )

    catalog = await ModelCatalogService(
        Settings(),
        admin=admin,
    ).list_models()

    by_id = {model.id: model for model in catalog.models}
    assert by_id["ollama/qwen3:8b"].owned_by == "ollama"
    assert by_id["ollama/qwen3:8b"].source == "bifrost"
    assert "ollama/gemma3:12b" in by_id
