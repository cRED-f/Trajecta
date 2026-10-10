"""Fail-closed protection against a model echoing internal context as its answer.

This is not a general secret/PII scanner (the existing input/context
middleware handles those concerns). It protects the streaming boundary against
recognisable internal session summaries and role/system prompt preambles,
including when a provider emits one character per streaming chunk.
"""

from __future__ import annotations


class InternalContextExposure(RuntimeError):
    """An internal-context preamble was emitted as assistant-visible text."""


_INTERNAL_PREFIXES = (
    "## session intent",
    "# session intent",
    "session intent\n",
    "## internal session summary",
    "## internal context",
    "## hidden system prompt",
    "## system prompt",
    "### system prompt",
    "system prompt:\n",
    "### developer instructions",
    "## developer instructions",
    "<|im_start|>system",
    "[im_start]system",
    "[start_header_id]system[end_header_id]",
    "you are trajecta, a local-first autonomous desktop agent",
    # A recognisable format seen in compaction payloads; an ordinary user
    # answer beginning "## Summary" is NOT blocked by this specific check.
    "## summary\n\n**user profile",
    "## next steps\n\nthe last user question was",
)


def contains_internal_context(text: str) -> bool:
    """Detect known leaked prompt/compaction artifacts in saved old records.

    Unlike the streaming prefix guard, the historical-data check searches the
    full record because episodic summaries embed previous answers mid-text.
    """
    lower = text.casefold()
    return any(marker in lower for marker in (
        "## session intent",
        "## internal session summary",
        "<|im_start|>system",
        "you are trajecta, a local-first autonomous desktop agent",
    ))


class VisibleTextGate:
    """Tiny bounded prefix buffer preventing a leaked preamble from streaming.

    After an ordinary answer is identified the gate becomes transparent. The
    normal latency cost is the first few characters needed to disambiguate a
    prefix. A rejected answer must not be saved or streamed even partially.
    """

    MAX_PREFIX_CHARS = 180

    def __init__(self) -> None:
        self._prefix = ""
        self._released = False

    @property
    def released(self) -> bool:
        return self._released

    def feed(self, chunk: str) -> str:
        if not chunk:
            return ""
        if self._released:
            return chunk
        self._prefix += chunk
        probe = self._prefix.lstrip("\ufeff \t\r\n").casefold()
        if any(probe.startswith(marker) for marker in _INTERNAL_PREFIXES):
            raise InternalContextExposure("Internal context appeared in the model output")
        if len(self._prefix) < self.MAX_PREFIX_CHARS and (
            not probe or any(marker.startswith(probe) for marker in _INTERNAL_PREFIXES)
        ):
            return ""
        return self.finish()

    def finish(self) -> str:
        """Flush a short, non-leaking legitimate answer on stream completion."""
        if self._released:
            return ""
        result = self._prefix
        self._prefix = ""
        self._released = True
        return result
