"""Offline regression checks for Phase 2 instrumentation and privacy."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from server.src.observability import bifrost, operations


@pytest.fixture
def spans(monkeypatch):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(operations, "tracer", provider.get_tracer("phase2"))
    monkeypatch.setattr(bifrost.trace, "get_current_span", trace.get_current_span)
    yield provider, exporter
    provider.shutdown()


@pytest.mark.asyncio
async def test_operation_span_nested_without_content(spans):
    provider, exporter = spans

    @operations.observed("trajecta.memory.semantic", result_count=True)
    async def search(query):
        assert query == "secret-user-memories"
        return [{"content": "classified"}, {"content": "another private value"}]

    with provider.get_tracer("test").start_as_current_span("agent") as agent:
        assert len(await search("secret-user-memories")) == 2
    found = exporter.get_finished_spans()
    child = next(s for s in found if s.name == "trajecta.memory.semantic")
    assert child.parent.span_id == agent.get_span_context().span_id
    assert child.attributes == {"trajecta.result_count": 2}
    assert "secret" not in str(child.attributes)


@pytest.mark.asyncio
async def test_operation_error_preserves_type_and_not_message(spans):
    _provider, exporter = spans

    @operations.observed("trajecta.learning.reflect")
    async def failing():
        raise ValueError("top-secret-prompt")

    with pytest.raises(ValueError, match="top-secret-prompt"):
        await failing()
    item = exporter.get_finished_spans()[-1]
    assert item.attributes["trajecta.error_type"] == "ValueError"
    assert item.status.status_code == StatusCode.ERROR
    assert "top-secret" not in str(item.attributes)
    assert "top-secret" not in str(item.events)


@pytest.mark.asyncio
async def test_cancellation_propagates_unchanged(spans):
    _provider, exporter = spans

    @operations.observed("trajecta.learning.run_once")
    async def stop():
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await stop()
    assert exporter.get_finished_spans()[-1].attributes["trajecta.outcome"] == "cancelled"


@pytest.mark.asyncio
async def test_guardrail_findings_export_only_action_enum(spans):
    _provider, exporter = spans

    @operations.observed("trajecta.guardrails.untrusted")
    async def protect(text):
        return SimpleNamespace(
            text="private response",
            findings=[SimpleNamespace(action="block", message="private secret")],
        )

    await protect("confidential input")
    item = exporter.get_finished_spans()[-1]
    assert item.attributes["trajecta.guardrails.findings"] == 1
    assert item.attributes["trajecta.guardrails.action"] == "block"
    assert "private" not in str(item.attributes)


@pytest.mark.asyncio
async def test_bifrost_injection_scoped_and_disablable(monkeypatch, spans):
    provider, _exporter = spans
    request = SimpleNamespace(headers={"Authorization": "Bearer private"})
    monkeypatch.delenv("TRAJECTA_PHOENIX_ENABLED", raising=False)
    with provider.get_tracer("test").start_as_current_span("agent"):
        bifrost.inject_bifrost_context(request)
        assert "traceparent" not in request.headers
        monkeypatch.setenv("TRAJECTA_PHOENIX_ENABLED", "true")
        bifrost.inject_bifrost_context(request)
        assert request.headers["traceparent"].startswith("00-")
        original = request.headers["traceparent"]
        await bifrost.inject_bifrost_context_async(request)
        assert request.headers["traceparent"] == original
    assert request.headers["Authorization"] == "Bearer private"


def test_no_traceparent_without_valid_span(monkeypatch):
    monkeypatch.setenv("TRAJECTA_PHOENIX_ENABLED", "true")
    request = SimpleNamespace(headers={})
    bifrost.inject_bifrost_context(request)
    assert request.headers == {}
