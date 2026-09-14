"""Runnable verification — exercises the full Router → Provider → LLMClient pipeline
without real API keys. Uses a FakeClient to prove the plumbing works.

Run: python -m server.src.llm_gateway.verify
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass

from server.src.llm_gateway.clients import (
    ChatMessage,
    CompletionEvent,
    CompletionResult,
    LLMClient,
    Usage,
)
from server.src.llm_gateway.routing.router import Router, RouteRule
from server.src.llm_gateway.routing.usage import UsageTracker


class FakeClient(LLMClient):
    """Deterministic client that returns canned responses — no network calls."""

    provider = "fake"

    def complete(self, messages, *, model=None, temperature=0.0, max_tokens=None):
        return CompletionResult(
            text=f"fake-response from {model}",
            model=model or "fake-model",
            usage=Usage(input_tokens=10, output_tokens=5),
        )

    def stream(self, messages, *, model=None, temperature=0.0, max_tokens=None):
        yield CompletionEvent(kind="start", model=model)
        yield CompletionEvent(kind="token", text="hello ")
        yield CompletionEvent(kind="token", text="world")
        yield CompletionEvent(
            kind="end",
            model=model,
            usage=Usage(input_tokens=10, output_tokens=5),
        )

    async def acomplete(self, messages, *, model=None, temperature=0.0, max_tokens=None):
        return self.complete(messages, model=model, temperature=temperature, max_tokens=max_tokens)

    async def astream(self, messages, *, model=None, temperature=0.0, max_tokens=None):
        for event in self.stream(messages, model=model, temperature=temperature, max_tokens=max_tokens):
            yield event


def _test_complete() -> bool:
    router = Router()
    router.register("fake", FakeClient(), is_default=True)
    router.add_rule(RouteRule(prefix="fake", provider_name="fake", fallbacks=[]))

    result = router.complete([("user", "hi")], model="fake:test-model")
    assert result.text == "fake-response from test-model", f"unexpected text: {result.text}"
    assert result.usage.input_tokens == 10
    assert result.usage.output_tokens == 5
    print(f"  [PASS] complete: {result.text}")
    return True


def _test_stream() -> bool:
    tracker = UsageTracker()
    router = Router(usage_tracker=tracker)
    router.register("fake", FakeClient(), is_default=True)
    router.add_rule(RouteRule(prefix="fake", provider_name="fake", fallbacks=[]))

    events = list(router.stream([("user", "hi")], model="fake:test-model", task_id="t1"))
    kinds = [e.kind for e in events]
    text = "".join(e.text for e in events if e.kind == "token")
    assert kinds == ["start", "token", "token", "end"], f"unexpected kinds: {kinds}"
    assert text == "hello world"
    # Usage was recorded
    usage = tracker.get_provider_usage("fake")
    assert usage.calls == 1
    assert usage.input_tokens == 10
    print(f"  [PASS] stream: '{text}', usage recorded (calls={usage.calls})")
    return True


def _test_fallback() -> bool:
    """Primary provider fails → falls back to secondary."""
    class FailingClient(LLMClient):
        provider = "fail"
        def complete(self, messages, **kw): raise ConnectionError("boom")
        def stream(self, messages, **kw): raise ConnectionError("boom")
        async def acomplete(self, messages, **kw): raise ConnectionError("boom")
        async def astream(self, messages, **kw): raise ConnectionError("boom")

    router = Router()
    router.register("fail", FailingClient())
    router.register("fake", FakeClient(), is_default=False)
    router.add_rule(RouteRule(prefix="fail", provider_name="fail", fallbacks=["fake"]))

    result = router.complete([("user", "hi")], model="fail:gpt")
    assert result.text == "fake-response from gpt"
    print(f"  [PASS] fallback: primary failed, got: {result.text}")
    return True


def _test_anthropic() -> bool:
    """Anthropic SDK formats system messages differently — provider must split
    them out. Patch the SDK's client factory and capture the kwargs.
    """
    from unittest.mock import Mock, patch

    captured: dict = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return Mock(
            model="claude-sonnet-4-6",
            content=[Mock(text="ok")],
            usage=Mock(input_tokens=5, output_tokens=5),
        )

    client = Mock()
    client.messages.create = fake_create
    with patch("anthropic.Anthropic", return_value=client):
        from server.src.llm_gateway.providers.anthropic import AnthropicClient

        anthropic_client = AnthropicClient(api_key="test-key", model="anthropic:claude-sonnet-4-6")
        result = anthropic_client.complete(
            [("system", "You are helpful"), ("user", "hello")]
        )

    assert result.text == "ok"
    # system message went to the `system` kwarg, not the messages list
    assert captured["system"] == "You are helpful"
    assert captured["messages"] == [{"role": "user", "content": "hello"}]
    print("  [PASS] anthropic: system split out, result ok")
    return True


def _test_default_resolution() -> bool:
    """No model string → uses default provider."""
    router = Router()
    router.register("fake", FakeClient(), is_default=True)
    result = router.complete([("user", "hi")])
    assert "fake" in result.model
    print(f"  [PASS] default resolution: model={result.model}")
    return True


def main() -> None:
    print("LLM Gateway verification")
    print("=" * 40)
    checks = [_test_complete, _test_stream, _test_fallback, _test_default_resolution, _test_anthropic]
    passed = sum(1 for check in checks if check())
    print("=" * 40)
    print(f"Results: {passed}/{len(checks)} passed")
    if passed < len(checks):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
