"""Safe OpenTelemetry spans for preparation and streaming agent runs.

A streamed run must be traced where its iterator is CONSUMED, not when the
async-generator object is constructed. This also includes scheduled jobs.
"""
from __future__ import annotations

import asyncio
import functools
import inspect
from collections.abc import AsyncIterator, Callable
from contextlib import aclosing
from typing import Any, TypeVar

from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

tracer = trace.get_tracer("trajecta.agent")
_F = TypeVar("_F", bound=Callable[..., Any])


def current_trace_id() -> str | None:
    context = trace.get_current_span().get_span_context()
    if not context.is_valid or not context.trace_flags.sampled:
        return None
    return f"{context.trace_id:032x}"


def _set_failure(span: Any, exc: BaseException) -> None:
    # Never record exception messages/stacktraces: they may contain raw
    # prompts, filenames, tool results, credentials, or private memory.
    span.set_attribute("trajecta.error_type", type(exc).__name__)
    span.set_status(Status(StatusCode.ERROR))


def trace_preparation(fn: _F) -> _F:
    """Wrap DeepAgentRuntime.prepare without storing user content."""
    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        conversation = kwargs.get("conversation")
        with tracer.start_as_current_span(
            "trajecta.agent.prepare",
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            if conversation is not None:
                span.set_attribute("trajecta.conversation_id", str(conversation.id))
            try:
                prepared = await fn(*args, **kwargs)
                span.set_attribute("trajecta.model", prepared.model_name)
                span.set_attribute("trajecta.preflight_ms", prepared.prepare_ms)
                return prepared
            except BaseException as exc:
                _set_failure(span, exc)
                raise

    return wrapper  # type: ignore[return-value]


def trace_agent_stream(fn: _F) -> _F:
    """Trace the whole streamed service run, including trajectory persistence.

    Keep the iterator's span active across model/tool await points. Explicit
    aclosing covers disconnections and early iterator closure.
    """
    if not inspect.isasyncgenfunction(fn):
        raise TypeError("trace_agent_stream expects an async generator")

    @functools.wraps(fn)
    async def wrapper(self: Any, turn: Any) -> AsyncIterator[Any]:
        resumed = fn.__name__ == "stream_resume"
        with tracer.start_as_current_span(
            "trajecta.agent.resume" if resumed else "trajecta.agent.run",
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            span.set_attribute("trajecta.run_id", turn.run_id)
            span.set_attribute("trajecta.conversation_id", turn.conversation.id)
            span.set_attribute("trajecta.model", turn.runtime.model_name)
            span.set_attribute("trajecta.resumed", resumed)
            outcome = "abandoned"
            try:
                async with aclosing(fn(self, turn)) as events:
                    async for event in events:
                        if event.type == "run.started":
                            trace_id = current_trace_id()
                            if trace_id is not None:
                                event.data["trace_id"] = trace_id
                        elif event.type == "run.finished":
                            for metric in ("ttft_seconds", "duration_seconds"):
                                value = event.data.get(metric)
                                if isinstance(value, (int, float)) and value >= 0:
                                    span.set_attribute(f"trajecta.{metric}", value)
                        elif event.type == "message.completed":
                            outcome = "completed"
                        elif event.type == "run.interrupted":
                            outcome = "interrupted"
                        elif event.type == "run.cancelled":
                            outcome = "cancelled"
                        elif event.type == "run.error":
                            outcome = "error"
                            span.set_status(Status(StatusCode.ERROR))
                            code = event.data.get("code")
                            if isinstance(code, str) and len(code) <= 80:
                                span.set_attribute("trajecta.error_code", code)
                        yield event
            except (asyncio.CancelledError, GeneratorExit):
                outcome = "cancelled"
                raise
            except BaseException as exc:
                outcome = "error"
                _set_failure(span, exc)
                raise
            finally:
                span.set_attribute("trajecta.outcome", outcome)

    return wrapper  # type: ignore[return-value]
