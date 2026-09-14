"""Provider interface (LLMClient) — the uniform contract every provider implements.

Consumers use only this base type. Return shapes:

- complete -> CompletionResult                (full response + usage)
- stream   -> stream CompletionEvent chunks   (start / token / end)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from typing import Any

# A single chat message: (role, content).
ChatMessage = tuple[str, str]


@dataclass(slots=True)
class Usage:
    """Token + cost accounting for one request."""

    input_tokens: int = 0
    output_tokens: int = 0
    # model dollars at the moment of the call, when known
    cost_usd: float | None = None

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def to_json(self) -> dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "cost_usd": self.cost_usd,
        }


@dataclass(slots=True)
class CompletionResult:
    """A completed (non-streaming) model response."""

    text: str
    model: str
    usage: Usage = field(default_factory=Usage)
    raw: Any | None = None


# -- Streaming event stream ------------------------------------------------


@dataclass(slots=True)
class CompletionEvent:
    """One event in a streaming response."""

    kind: str  # "start" | "token" | "end"
    text: str = ""
    model: str | None = None
    usage: Usage | None = None
    raw: Any | None = None


class LLMClient(ABC):
    """Uniform chat-completion interface over any provider.

    Subclasses wrap a real SDK (OpenAI, Anthropic, Ollama, ...).
    """

    provider: str = "base"

    @abstractmethod
    def complete(
        self,
        messages: list[ChatMessage],
        *,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> CompletionResult:
        """Full non-streaming completion. Returns the complete text + usage."""

    @abstractmethod
    def stream(
        self,
        messages: list[ChatMessage],
        *,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> Iterator[CompletionEvent]:
        """Streaming completion. Yields CompletionEvent objects (start/token/end)."""

    @abstractmethod
    async def acomplete(
        self,
        messages: list[ChatMessage],
        *,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> CompletionResult:
        """Async full completion."""

    @abstractmethod
    async def astream(
        self,
        messages: list[ChatMessage],
        *,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> AsyncIterator[CompletionEvent]:
        """Async streaming completion."""