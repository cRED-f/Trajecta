"""Anthropic provider — concrete LLMClient over the `anthropic` SDK.

Used when Bifrost is not running (local dev) or when direct Anthropic access
is needed (Bifrost handles Anthropic routing in production).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any

import anthropic as _anthropic

from server.src.llm_gateway.clients import (
    ChatMessage,
    CompletionEvent,
    CompletionResult,
    LLMClient,
    Usage,
)


class AnthropicClient(LLMClient):
    """Chat completions via the Anthropic SDK."""

    provider = "anthropic"

    def __init__(
        self,
        api_key: str | None = None,
        *,
        model: str | None = None,
        base_url: str | None = None,
    ) -> None:
        self._model = model
        self._client = _anthropic.Anthropic(api_key=api_key, base_url=base_url)
        self._aclient = _anthropic.AsyncAnthropic(api_key=api_key, base_url=base_url)

    # -- internal helpers ---------------------------------------------------

    def _build_kwargs(
        self, model: str | None, temperature: float, max_tokens: int | None
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {"model": model or self._model, "temperature": temperature}
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        else:
            kwargs["max_tokens"] = 4096
        return kwargs

    @staticmethod
    def _to_result(resp: Any) -> CompletionResult:
        usage = Usage(
            input_tokens=getattr(resp.usage, "input_tokens", 0),
            output_tokens=getattr(resp.usage, "output_tokens", 0),
        )
        return CompletionResult(
            text=resp.content[0].text if resp.content else "",
            model=resp.model,
            usage=usage,
            raw=resp,
        )

    @staticmethod
    def _to_events(stream: Any, model: str) -> Iterator[CompletionEvent]:
        yield CompletionEvent(kind="start", model=model)
        for event in stream:
            if event.type == "content_block_delta" and event.delta.type == "text_delta":
                yield CompletionEvent(kind="token", text=event.delta.text)
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
        # Anthropic uses a separate system param, not a system message
        system = ""
        chat_messages = []
        for role, content in messages:
            if role == "system":
                system = content
            else:
                chat_messages.append({"role": role, "content": content})
        if not chat_messages:
            chat_messages = [{"role": "user", "content": ""}]
        kwargs = self._build_kwargs(model, temperature, max_tokens)
        if system:
            kwargs["system"] = system
        resp = self._client.messages.create(messages=chat_messages, **kwargs)
        return self._to_result(resp)

    def stream(
        self,
        messages: list[ChatMessage],
        *,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> Iterator[CompletionEvent]:
        system = ""
        chat_messages = []
        for role, content in messages:
            if role == "system":
                system = content
            else:
                chat_messages.append({"role": role, "content": content})
        if not chat_messages:
            chat_messages = [{"role": "user", "content": ""}]
        kwargs = self._build_kwargs(model, temperature, max_tokens)
        if system:
            kwargs["system"] = system
        with self._client.messages.stream(**kwargs) as s:
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
        system = ""
        chat_messages = []
        for role, content in messages:
            if role == "system":
                system = content
            else:
                chat_messages.append({"role": role, "content": content})
        if not chat_messages:
            chat_messages = [{"role": "user", "content": ""}]
        kwargs = self._build_kwargs(model, temperature, max_tokens)
        if system:
            kwargs["system"] = system
        resp = await self._aclient.messages.create(messages=chat_messages, **kwargs)
        return self._to_result(resp)

    async def astream(
        self,
        messages: list[ChatMessage],
        *,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> AsyncIterator[CompletionEvent]:
        system = ""
        chat_messages = []
        for role, content in messages:
            if role == "system":
                system = content
            else:
                chat_messages.append({"role": role, "content": content})
        if not chat_messages:
            chat_messages = [{"role": "user", "content": ""}]
        kwargs = self._build_kwargs(model, temperature, max_tokens)
        if system:
            kwargs["system"] = system
        async with self._aclient.messages.stream(**kwargs) as s:
            yield CompletionEvent(kind="start", model=model or self._model)
            async for event in s:
                if event.type == "content_block_delta" and event.delta.type == "text_delta":
                    yield CompletionEvent(kind="token", text=event.delta.text)
            yield CompletionEvent(kind="end", model=model or self._model)