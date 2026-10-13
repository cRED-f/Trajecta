"""Offline contract tests. Never contact Phoenix or an LLM provider."""
from __future__ import annotations

import asyncio
import sys
import types
from contextlib import aclosing
from types import SimpleNamespace

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import ProxyTracerProvider, StatusCode

from server.src.observability import setup
from server.src.observability import tracing


@pytest.fixture
def spans(monkeypatch):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(tracing, "tracer", provider.get_tracer("trajecta-test"))
    yield exporter
    provider.shutdown()


def _turn():
    return SimpleNamespace(
        run_id="real-service-id",
        conversation=SimpleNamespace(id="conversation-123"),
        runtime=SimpleNamespace(model_name="test-llm"),
    )


@pytest.mark.asyncio
async def test_agent_trace_stream_and_child_calls(spans):
    async def stream(_self, _turn):
        # Child spans simulate LangChain callback instrumentation.
        with tracing.tracer.start_as_current_span("langchain.model"):
            yield SimpleNamespace(type="run.started", data={})
        yield SimpleNamespace(type="message.completed", data={})

    wrapped = tracing.trace_agent_stream(stream)
    events = [event async for event in wrapped(None, _turn())]
    completed = spans.get_finished_spans()
    root = next(s for s in completed if s.name == "trajecta.agent.run")
    child = next(s for s in completed if s.name == "langchain.model")
    assert child.parent.span_id == root.context.span_id
    assert root.attributes["trajecta.run_id"] == "real-service-id"
    assert root.attributes["trajecta.outcome"] == "completed"
    assert root.attributes["trajecta.conversation_id"] == "conversation-123"
    assert events[0].data["trace_id"] == f"{root.context.trace_id:032x}"


@pytest.mark.asyncio
@pytest.mark.parametrize("event_type,outcome,error", [
    ("run.interrupted", "interrupted", False),
    ("run.cancelled", "cancelled", False),
    ("run.error", "error", True),
])
async def test_terminal_outcomes(spans, event_type, outcome, error):
    async def stream(_self, _turn):
        yield SimpleNamespace(type="run.started", data={})
        yield SimpleNamespace(type=event_type, data={"error": "password-secret-value"})

    events = [e async for e in tracing.trace_agent_stream(stream)(None, _turn())]
    assert events[-1].type == event_type
    root = next(s for s in spans.get_finished_spans() if s.name == "trajecta.agent.run")
    assert root.attributes["trajecta.outcome"] == outcome
    assert (root.status.status_code == StatusCode.ERROR) == error
    assert "password-secret-value" not in str(root.attributes)


@pytest.mark.asyncio
async def test_early_closed_generator_marks_cancelled_and_closes_source(spans):
    was_closed = False

    async def stream(_self, _turn):
        nonlocal was_closed
        try:
            yield SimpleNamespace(type="run.started", data={})
            await asyncio.Event().wait()
        finally:
            was_closed = True

    wrapped = tracing.trace_agent_stream(stream)
    async with aclosing(wrapped(None, _turn())) as iterator:
        first = await anext(iterator)
        assert first.type == "run.started"
    assert was_closed
    root = spans.get_finished_spans()[-1]
    assert root.attributes["trajecta.outcome"] == "cancelled"


@pytest.mark.asyncio
async def test_resume_trace_and_preflight_no_content(spans):
    @tracing.trace_preparation
    async def prepare(*, conversation, task_text):
        assert task_text == "TOP SECRET user content"
        return SimpleNamespace(model_name="test-llm", prepare_ms=123.0)

    @tracing.trace_agent_stream
    async def stream_resume(_self, _turn):
        yield SimpleNamespace(type="run.started", data={})
        yield SimpleNamespace(type="run.interrupted", data={})

    await prepare(conversation=SimpleNamespace(id="conv"), task_text="TOP SECRET user content")
    [event async for event in stream_resume(None, _turn())]
    exported = spans.get_finished_spans()
    assert {s.name for s in exported} == {"trajecta.agent.prepare", "trajecta.agent.resume"}
    assert all("TOP SECRET" not in str(span.attributes) for span in exported)
    assert exported[-1].attributes["trajecta.resumed"] is True


@pytest.mark.asyncio
async def test_stream_exception_reports_type_not_secret(spans):
    async def stream(_self, _turn):
        yield SimpleNamespace(type="run.started", data={})
        raise RuntimeError("credential=secret-token")

    with pytest.raises(RuntimeError):
        [event async for event in tracing.trace_agent_stream(stream)(None, _turn())]
    root = spans.get_finished_spans()[-1]
    assert root.attributes["trajecta.outcome"] == "error"
    assert root.attributes["trajecta.error_type"] == "RuntimeError"
    assert "secret-token" not in str(root.attributes)
    assert not root.events  # no exception event with full stacktrace


def test_disabled_or_missing_phoenix_does_not_start(monkeypatch):
    monkeypatch.delenv("TRAJECTA_PHOENIX_ENABLED", raising=False)
    assert not setup.initialize_observability()
    monkeypatch.setenv("TRAJECTA_PHOENIX_ENABLED", "true")
    monkeypatch.setattr(setup.trace, "get_tracer_provider", lambda: ProxyTracerProvider())
    monkeypatch.setitem(sys.modules, "phoenix", None)
    assert not setup.initialize_observability()


def test_initialize_once_and_privacy_controls(monkeypatch):
    monkeypatch.setenv("TRAJECTA_PHOENIX_ENABLED", "true")
    monkeypatch.setenv("TRAJECTA_PHOENIX_ENDPOINT", "http://127.0.0.1:6006/v1/traces")
    monkeypatch.setattr(setup.trace, "get_tracer_provider", lambda: ProxyTracerProvider())
    calls = []

    class Provider:
        def shutdown(self):
            calls.append("shutdown")

    provider = Provider()

    def register(**kwargs):
        calls.append(("register", kwargs))
        return provider

    class TraceConfig:
        def __init__(self, **kwargs):
            calls.append(("privacy", kwargs))

    class LangChainInstrumentor:
        def instrument(self, **kwargs):
            calls.append(("instrument", kwargs))
        def uninstrument(self):
            calls.append("uninstrument")

    monkeypatch.setitem(sys.modules, "phoenix", types.ModuleType("phoenix"))
    ph_otel = types.ModuleType("phoenix.otel")
    ph_otel.register = register
    monkeypatch.setitem(sys.modules, "phoenix.otel", ph_otel)
    fake_oi = types.ModuleType("openinference")
    fake_oi_instr = types.ModuleType("openinference.instrumentation")
    fake_oi_instr.TraceConfig = TraceConfig
    fake_lc = types.ModuleType("openinference.instrumentation.langchain")
    fake_lc.LangChainInstrumentor = LangChainInstrumentor
    monkeypatch.setitem(sys.modules, "openinference", fake_oi)
    monkeypatch.setitem(sys.modules, "openinference.instrumentation", fake_oi_instr)
    monkeypatch.setitem(sys.modules, "openinference.instrumentation.langchain", fake_lc)

    try:
        assert setup.initialize_observability() is True
        assert setup.initialize_observability() is True
        assert sum(isinstance(x, tuple) and x[0] == "register" for x in calls) == 1
        assert next(x[1] for x in calls if isinstance(x, tuple) and x[0] == "privacy")["hide_inputs"]
        assert next(x[1] for x in calls if isinstance(x, tuple) and x[0] == "privacy")["hide_outputs"]
        assert next(x[1] for x in calls if isinstance(x, tuple) and x[0] == "register")["auto_instrument"] is False
    finally:
        setup.shutdown_observability()
    assert "shutdown" in calls
    assert "uninstrument" in calls


def test_remote_endpoint_rejected_by_default(monkeypatch):
    monkeypatch.setenv("TRAJECTA_PHOENIX_ENABLED", "true")
    monkeypatch.setenv("TRAJECTA_PHOENIX_ENDPOINT", "https://thirdparty.example/v1/traces")
    monkeypatch.delenv("TRAJECTA_PHOENIX_ALLOW_REMOTE", raising=False)
    monkeypatch.setattr(setup.trace, "get_tracer_provider", lambda: ProxyTracerProvider())
    assert setup.initialize_observability() is False
