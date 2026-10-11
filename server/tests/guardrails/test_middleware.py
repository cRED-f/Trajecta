"""Verify middleware consumes a single policy snapshot per model call."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

from server.src.config import Settings
from server.src.guardrails.content import ContentGuardrailService
from server.src.guardrails.middleware import GuardrailsModelMiddleware


@pytest.mark.asyncio
async def test_one_settings_read_for_many_messages(monkeypatch) -> None:
    class FakePolicyStore:
        async def get_setting(self, _key, default=None):
            return default

    service = ContentGuardrailService(Settings(), FakePolicyStore())  # type: ignore[arg-type]
    original_get = service.get_settings
    reads = 0

    async def counted_get():
        nonlocal reads
        reads += 1
        return await original_get()

    monkeypatch.setattr(service, "get_settings", counted_get)

    async def fake_untrusted(text, *, cfg=None):
        from server.src.guardrails.content import GuardedText
        return GuardedText(text=text)

    async def fake_privacy(text, *, model_name, cfg=None):
        from server.src.guardrails.content import GuardedText
        return GuardedText(text=text)

    monkeypatch.setattr(service, "guard_untrusted_text", fake_untrusted)
    monkeypatch.setattr(service, "protect_model_context", fake_privacy)

    request = SimpleNamespace(
        messages=[HumanMessage(content="hello"), ToolMessage(content="tool", tool_call_id="1")],
        system_message=SystemMessage(content="system"),
    )

    async def handler(req):
        assert req is request
        return "ok"

    middleware = GuardrailsModelMiddleware(service, model_name="openai/test")
    assert await middleware.awrap_model_call(request, handler) == "ok"
    assert reads == 1
