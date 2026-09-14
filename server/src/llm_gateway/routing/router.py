"""Router — selects a provider by task type and model string, with fallback chains."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from server.src.llm_gateway.clients import LLMClient, ChatMessage, CompletionResult, CompletionEvent
from server.src.llm_gateway.routing.usage import UsageTracker


@dataclass
class RouteRule:
    """A single routing rule: match a prefix (e.g. 'openai:', 'anthropic:', 'ollama:') to a provider."""

    prefix: str
    provider_name: str
    fallbacks: list[str] = field(default_factory=list)


class Router:
    """Dispatches model calls to the correct LLMClient based on model string prefix.

    Model strings use the format `provider:model` (e.g. `openai:gpt-5.5`,
    `anthropic:claude-sonnet-4-6`, `ollama:north-mini-code-1.0`).

    Falls back through the chain on error.
    """

    def __init__(self, usage_tracker: UsageTracker | None = None) -> None:
        self._clients: dict[str, LLMClient] = {}
        self._rules: list[RouteRule] = []
        self._default_provider: str | None = None
        self._usage = usage_tracker or UsageTracker()

    # -- registration -------------------------------------------------------

    def register(self, name: str, client: LLMClient, *, is_default: bool = False) -> None:
        self._clients[name] = client
        if is_default:
            self._default_provider = name

    def add_rule(self, rule: RouteRule) -> None:
        self._rules.append(rule)

    # -- resolution ---------------------------------------------------------

    def _resolve(self, model: str | None) -> tuple[str, str]:
        """Returns (provider_name, model_name) from a `provider:model` string."""
        if model and ":" in model:
            prefix, model_name = model.split(":", 1)
            for rule in self._rules:
                if prefix == rule.prefix:
                    return rule.provider_name, model_name
            # direct name match (no prefix rule)
            if prefix in self._clients:
                return prefix, model_name
        # fallback to default
        if self._default_provider:
            return self._default_provider, model or ""
        raise ValueError(f"No provider registered and no rule matches model={model!r}")

    def _fallback_chain(self, provider_name: str) -> Iterator[str]:
        """Yields provider names in fallback order."""
        yield provider_name
        for rule in self._rules:
            if rule.provider_name == provider_name:
                yield from rule.fallbacks
                return

    # -- dispatch ------------------------------------------------------------

    def complete(
        self,
        messages: list[ChatMessage],
        *,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        task_id: str | None = None,
    ) -> CompletionResult:
        provider_name, model_name = self._resolve(model)
        last_err: Exception | None = None
        for pname in self._fallback_chain(provider_name):
            client = self._clients.get(pname)
            if client is None:
                continue
            try:
                result = client.complete(
                    messages, model=model_name, temperature=temperature, max_tokens=max_tokens
                )
                self._usage.record(pname, result.usage, task_id=task_id)
                return result
            except Exception as e:
                last_err = e
                continue
        raise RuntimeError(f"All providers failed for model={model!r}") from last_err

    def stream(
        self,
        messages: list[ChatMessage],
        *,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        task_id: str | None = None,
    ) -> Iterator[CompletionEvent]:
        provider_name, model_name = self._resolve(model)
        last_err: Exception | None = None
        for pname in self._fallback_chain(provider_name):
            client = self._clients.get(pname)
            if client is None:
                continue
            try:
                for event in client.stream(
                    messages, model=model_name, temperature=temperature, max_tokens=max_tokens
                ):
                    if event.kind == "end" and event.usage:
                        self._usage.record(pname, event.usage, task_id=task_id)
                    yield event
                return
            except Exception as e:
                last_err = e
                continue
        raise RuntimeError(f"All providers failed for model={model!r}") from last_err