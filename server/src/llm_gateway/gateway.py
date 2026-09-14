"""Gateway orchestrator — builds the Router + providers from config.

In production, Bifrost is the sidecar: one OpenAICompatClient pointed at
Bifrost's HTTP endpoint, and Bifrost does routing/failover/caching.

In local dev without Bifrost, individual providers (OpenAICompatClient for
OpenAI, AnthropicClient for Anthropic, etc.) are wired directly.
"""

from __future__ import annotations

import os

from server.src.config import LlmProviderConfig, Settings
from server.src.llm_gateway.clients import LLMClient
from server.src.llm_gateway.providers.openai_compat import OpenAICompatClient
from server.src.llm_gateway.providers.anthropic import AnthropicClient
from server.src.llm_gateway.routing.router import Router, RouteRule
from server.src.llm_gateway.routing.usage import UsageTracker

_gateway: Router | None = None


def get_gateway(settings: Settings | None = None) -> Router:
    """Return the singleton Router. Builds it on first call from settings."""
    global _gateway
    if _gateway is not None:
        return _gateway
    _gateway = _build_router(settings)
    return _gateway


def reset_gateway() -> None:
    """Reset the singleton (for testing)."""
    global _gateway
    _gateway = None


def _build_router(settings: Settings | None = None) -> Router:
    if settings is None:
        settings = Settings.load()

    llm = settings.llm
    tracker = UsageTracker()
    router = Router(usage_tracker=tracker)

    # Bifrost sidecar mode: one OpenAICompatClient → Bifrost's /v1 endpoint.
    # Bifrost handles provider routing, failover, caching internally.
    # Credentials live in Bifrost config, not here — virtual key is just auth.
    bifrost_url = os.environ.get("TRAJECTA_BIFROST_URL") or _find_bifrost(settings)
    if bifrost_url:
        virtual_key = os.environ.get("BIFROST_VIRTUAL_KEY", "sk-bf-local")
        bifrost_client = OpenAICompatClient(
            api_key=virtual_key,
            base_url=f"{bifrost_url.rstrip('/')}/v1",
            model=None,  # model string passed at call time (e.g. "openai/gpt-4o-mini")
        )
        # Route ALL model strings through Bifrost — it parses provider/model itself.
        router.register("bifrost", bifrost_client, is_default=True)
        return router

    # Direct provider mode: wire each enabled provider individually.
    providers = llm.providers
    default_name = llm.default_provider

    # Build clients for every enabled provider; drop ones we can't instantiate.
    clients: dict[str, tuple[LLMClient, bool]] = {}
    for name, cfg in providers.items():
        if not cfg.enabled:
            continue
        client = _make_client(name, cfg)
        if client is not None:
            # If the configured default provider is disabled, fall back to the first enabled.
            is_default = (name == default_name) or (
                not any(p.enabled for n, p in providers.items() if n == default_name)
                and clients == {}
            )
            clients[name] = (client, is_default)

    if not clients:
        raise RuntimeError("No enabled LLM providers configured (check config/default.yaml llm section)")

    for name, (client, is_default) in clients.items():
        router.register(name, client, is_default=is_default)

    for name in clients:
        fallbacks = [n for n in clients if n != name]
        router.add_rule(RouteRule(prefix=name, provider_name=name, fallbacks=fallbacks))

    return router


def _find_bifrost(settings: Settings) -> str | None:
    """Check config for a Bifrost base_url. Returns None if disabled."""
    providers = settings.llm.providers
    bifrost_cfg = providers.get("bifrost")
    if bifrost_cfg and bifrost_cfg.enabled and bifrost_cfg.base_url:
        return bifrost_cfg.base_url
    return None


def _make_client(name: str, cfg: LlmProviderConfig) -> LLMClient | None:
    """Instantiate an LLMClient from a provider config entry."""
    api_key = os.environ.get(f"TRAJECTA_{name.upper()}_API_KEY", "")
    if name in ("openai", "omni_route", "9_router", "openai_compat"):
        return OpenAICompatClient(
            api_key=api_key or None,
            base_url=cfg.base_url,
            model=cfg.model,
        )
    if name == "anthropic":
        return AnthropicClient(
            api_key=api_key or None,
            model=cfg.model,
            base_url=cfg.base_url,
        )
    if name == "ollama":
        return OpenAICompatClient(
            api_key="local",
            base_url=cfg.base_url or "http://localhost:11434",
            model=cfg.model,
        )
    return None
