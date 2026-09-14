"""OpenAI-compatible provider — concrete LLMClient over the `openai` SDK.

Covers OpenAI, Ollama-OpenAI-bridge, OmniRoute, 9Router, and any
OpenAI-compatible endpoint, so adding more providers is just filling
the same interface.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any

import openai as _openai

from server.src.llm_gateway.clients import (
    ChatMessage,
    CompletionEvent,
    CompletionResult,
    LLMClient,
    Usage,
)


class OpenAICompatClient(LLMClient):
    """Chat completions via the openai SDK against any OpenAI-compatible base URL."""

    provider = "openai_compat"

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str | None = None,
        model: str | None = None,
        default_headers: dict[str, str] | None = None,
    ) -> None:
        self._model = model
        self._client = _openai.OpenAI(
            api_key=api_key or "local",
            base_url=base_url,
            default_headers=default_headers,
        )
        self._aclient = _openai.AsyncOpenAI(
            api_key=api_key or "local",
            base_url=base_url,
            default_headers=default_headers,
        )

    # -- internal helpers ---------------------------------------------------

    def _build_kwargs(
        self, model: str | None, temperature: float, max_tokens: int | None
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {"model": model or self._model, "temperature": temperature}
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        return kwargs

    @staticmethod
    def _to_result(resp: Any) -> CompletionResult:
        usage = Usage(
            input_tokens=getattr(resp.usage, "prompt_tokens", 0) if resp.usage else 0,
            output_tokens=getattr(resp.usage, "completion_tokens", 0) if resp.usage else 0,
        )
        return CompletionResult(
            text=resp.choices[0].message.content or "",
            model=resp.model,
            usage=usage,
            raw=resp,
        )

    @staticmethod
    def _to_events(stream: Any, model: str) -> Iterator[CompletionEvent]:
        yield CompletionEvent(kind="start", model=model)
        for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta and delta.content:
                yield CompletionEvent(kind="token", text=delta.content)
        yield CompletionEvent(kind="end", model=model)

    # -- sync interface -----------------------------------------------------

    def complete(
        self,
        messages: list[ChatMessage],
        *,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> CompletionResult:
        resp = self._client.chat.completions.create(
            messages=[{"role": role, "content": content} for role, content in messages],
            **self._build_kwargs(model, temperature, max_tokens),
        )
        return self._to_result(resp)

    def stream(
        self,
        messages: list[ChatMessage],
        *,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> Iterator[CompletionEvent]:
        s = self._client.chat.completions.create(
            messages=[{"role": role, "content": content} for role, content in messages],
            **self._build_kwargs(model, temperature, max_tokens),
            stream=True,
        )
        yield from self._to_events(s, model or self._model)

    # -- async interface ----------------------------------------------------

    async def acomplete(
        self,
        messages: list[ChatMessage],
        *,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> CompletionResult:
        resp = await self._aclient.chat.completions.create(
            messages=[{"role": role, "content": content} for role, content in messages],
            **self._build_kwargs(model, temperature, max_tokens),
        )
        return self._to_result(resp)

    async def astream(
        self,
        messages: list[ChatMessage],
        *,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> AsyncIterator[CompletionEvent]:
        s = await self._aclient.chat.completions.create(
            messages=[{"role": role, "content": content} for role, content in messages],
            **self._build_kwargs(model, temperature, max_tokens),
            stream=True,
        )
        yield CompletionEvent(kind="start", model=model or self._model)
        async for chunk in s:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta and delta.content:
                yield CompletionEvent(kind="token", text=delta.content)
        yield CompletionEvent(kind="end", model=model or self._model)