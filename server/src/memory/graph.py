"""Memory graph — served by `langgraph dev` for episodic memory.

A Deep Agents agent graph whose checkpointer + store persist threads and
`/memories/` files. `langgraph-sdk` clients in the Trajecta server query this
graph's threads (`threads.search`) for episodic memory.

The model comes from Settings (same source the LLM gateway uses), resolved the
same way — Bifrost URL + virtual key when Bifrost is enabled, else the default
provider. Everything is env-overridable; nothing hardcoded.
"""

from __future__ import annotations

import os

from langchain_openai import ChatOpenAI

from deepagents import create_deep_agent

from server.src.config import Settings


def _model() -> ChatOpenAI:
    """Resolve an LLM from Settings, mirroring the gateway's Bifrost resolution."""
    settings = Settings.load()
    llm = settings.llm
    provider_cfg = llm.providers.get(llm.default_provider)
    assert provider_cfg is not None, "no default LLM provider configured"

    # Env override wins; else the default provider's model.
    model_str = (
        os.environ.get(provider_cfg.model_env, "").strip()
        if provider_cfg.model_env
        else ""
    ) or provider_cfg.model

    # Bifrost sidecar: one OpenAI-compatible client → Bifrost /v1.
    bifrost_url = os.environ.get("TRAJECTA_BIFROST_URL") or ""
    for name, cfg in llm.providers.items():
        if name == "bifrost" and cfg.enabled and cfg.base_url:
            bifrost_url = bifrost_url or cfg.base_url
            break
    if bifrost_url:
        virtual_key = os.environ.get("BIFROST_VIRTUAL_KEY", "sk-bf-trajecta")
        return ChatOpenAI(
            model=model_str,
            base_url=f"{bifrost_url.rstrip('/')}/v1",
            api_key=virtual_key,
            temperature=0,
        )

    api_key = (
        os.environ.get(f"TRAJECTA_{llm.default_provider.upper()}_API_KEY", "")
        or "local"  # local gateways don't auth (same convention as OpenAICompatClient)
    )
    return ChatOpenAI(model=model_str, base_url=provider_cfg.base_url or None, api_key=api_key, temperature=0)


def memory_graph():
    """Build the Deep Agents graph the LangGraph server serves."""
    return create_deep_agent(
        model=_model(),
        memory=["/memories/AGENTS.md"],
        skills=["/skills/"],
    )


graph = memory_graph()