"""Regression tests for the streaming run loop in chat.runtime.

Covers two behaviors:
1. Stop must cancel a run that is blocked on a silent model stream
   (the old loop only polled cancel_event when a chunk arrived).
2. Assistant text streams straight to message.delta as the model
   emits it, before the model step completes -- no quarantine,
   no post-generation validation.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from langchain_core.messages import AIMessageChunk

from server.src.chat.models import Conversation
from server.src.chat.runtime import (
    DeepAgentRuntime,
    PreparedAgentRun,
)


class NeverYieldingAgent:
    """astream() blocks forever without emitting a single chunk."""

    async def astream(self, *_args: Any, **_kwargs: Any):
        await asyncio.Event().wait()
        yield  # pragma: no cover - never reached


class FakeAgent:
    """astream() replays a fixed sequence of graph stream events."""

    def __init__(self, events: list[dict[str, Any]]) -> None:
        self._events = events

    async def astream(self, *_args: Any, **_kwargs: Any):
        for event in self._events:
            yield event


class StubMemory:
    checkpointer = None


def _runtime() -> DeepAgentRuntime:
    runtime = DeepAgentRuntime.__new__(DeepAgentRuntime)
    runtime._memory = StubMemory()  # type: ignore[assignment]
    return runtime


def _conversation() -> Conversation:
    now = datetime.now(timezone.utc)
    return Conversation(
        id="conv-1",
        thread_id="thread-1",
        title="t",
        model="test-model",
        archived=False,
        created_at=now,
        updated_at=now,
    )


def _prepared(agent: Any) -> PreparedAgentRun:
    return PreparedAgentRun(
        agent=agent,
        config={"configurable": {"thread_id": "thread-1"}},
        model_name="test-model",
        mcp_tool_count=0,
        thread_id="thread-1",
    )


async def _consume(
    runtime: DeepAgentRuntime,
    prepared: PreparedAgentRun,
    cancel_event: asyncio.Event,
    events: list[Any],
) -> None:
    async for event in runtime._stream_input(
        prepared=prepared,
        conversation=_conversation(),
        agent_input={"messages": [{"role": "user", "content": "hi"}]},
        cancel_event=cancel_event,
        resumed=False,
    ):
        events.append(event)


async def test_stop_is_observed_while_model_stream_never_yields() -> None:
    runtime = _runtime()
    prepared = _prepared(NeverYieldingAgent())
    cancel_event = asyncio.Event()

    events: list[Any] = []
    task = asyncio.create_task(
        _consume(runtime, prepared, cancel_event, events)
    )

    # run.started needs nothing from the model, so it arrives while the
    # consumer is about to block inside the silent astream().
    for _ in range(200):
        if events:
            break
        await asyncio.sleep(0.005)
    assert events and events[0].type == "run.started"

    # User pressed Stop while the model has produced nothing at all.
    cancel_event.set()

    # The old async-for loop never noticed: it hung forever here.
    await asyncio.wait_for(task, timeout=5.0)

    types = [event.type for event in events]
    assert "run.cancelled" in types
    assert "run.error" not in types
    assert types[-1] == "run.cancelled"


async def test_each_chunk_streams_as_a_delta_before_model_step_completes() -> None:
    text = "The quick brown fox jumps over the lazy dog."
    chunk_size = 700
    events: list[dict[str, Any]] = [
        {
            "type": "messages",
            "ns": (),
            "data": (AIMessageChunk(content=text[offset : offset + chunk_size]), {}),
        }
        for offset in range(0, len(text), chunk_size)
    ]
    # Model step completes after the last chunk.
    events.append({"type": "updates", "ns": (), "data": {"agent": {}}})

    collected: list[Any] = []
    await asyncio.wait_for(
        _consume(_runtime(), _prepared(FakeAgent(events)), asyncio.Event(), collected),
        timeout=5.0,
    )

    types = [event.type for event in collected]
    delta_indexes = [
        index for index, kind in enumerate(types) if kind == "message.delta"
    ]
    finished = types.index("run.finished")

    # The first chunk reached the SSE stream while the model was still
    # generating -- before the updates event, with no buffering.
    first_step = types.index("agent.step")
    assert delta_indexes, "expected at least one streamed message.delta"
    assert delta_indexes[0] < first_step
    assert all(index < finished for index in delta_indexes)

    # One delta per model chunk: text flows through unchanged.
    assert len(delta_indexes) == len(
        [event for event in events if event["type"] == "messages"]
    )

    assembled = "".join(
        event.data["text"] for event in collected if event.type == "message.delta"
    )
    assert assembled == text

    # Deltas carry the main-agent source so the frontend attributes
    # them to the assistant response.
    deltas = [event for event in collected if event.type == "message.delta"]
    assert all(event.data.get("source") == "main" for event in deltas)


async def test_recoverable_web_404_is_activity_error_not_run_error() -> None:
    """A missing page must not prevent subsequent model output or completion."""
    import json
    from langchain_core.messages import ToolMessage

    missing_page = ToolMessage(
        name="web_extract",
        tool_call_id="web-1",
        content=json.dumps({
            "ok": False,
            "recoverable": True,
            "reason": "HTTP 404",
            "message": "Continue with another source",
        }),
    )
    events = [
        {"type": "messages", "ns": (), "data": (missing_page, {})},
        {"type": "messages", "ns": (), "data": (
            AIMessageChunk(content="The original page is unavailable; here are other universities."),
            {},
        )},
    ]
    collected: list[Any] = []
    await _consume(_runtime(), _prepared(FakeAgent(events)), asyncio.Event(), collected)

    assert "run.finished" in [event.type for event in collected]
    assert "run.error" not in [event.type for event in collected]
    failures = [event for event in collected if event.type == "tool.result"]
    assert len(failures) == 1
    assert failures[0].data["status"] == "error"
    assert "other universities" in "".join(
        event.data["text"] for event in collected if event.type == "message.delta"
    )
    finished = next(event for event in collected if event.type == "run.finished")
    assert finished.data["tool_errors"] == 1

class RecoveryGuardrails:
    """Simulate the existing input/observations model safety boundary."""

    async def protect_model_context(self, text: str, *, model_name: str):
        from types import SimpleNamespace
        return SimpleNamespace(text=text)

    async def guard_untrusted_text(self, text: str):
        from types import SimpleNamespace
        return SimpleNamespace(text=text)


class RecoveryModel:
    def __init__(self, chunks: list[str]):
        self.chunks = chunks
        self.calls = 0
        self.messages = None

    async def astream(self, messages):
        self.calls += 1
        self.messages = messages
        for part in self.chunks:
            yield AIMessageChunk(content=part)


class RecoveryModelFactory:
    def __init__(self, model):
        self.model = model

    def create(self, model_name: str):
        assert model_name == "test-model"
        return self.model


class CountingAgent(FakeAgent):
    def __init__(self, events):
        super().__init__(events)
        self.calls = 0

    async def astream(self, *args, **kwargs):
        self.calls += 1
        async for event in super().astream(*args, **kwargs):
            yield event


async def test_internal_summary_in_provider_reasoning_does_not_abort_chat() -> None:
    """The reasoning channel is distinct from the user-facing answer."""
    agent = CountingAgent([
        {"type": "messages", "ns": (), "data": (
            AIMessageChunk(content="", id="message-1", additional_kwargs={
                "reasoning_content": "## SESSION INTENT\nSome internal notes"
            }), {},
        )},
        {"type": "messages", "ns": (), "data": (
            AIMessageChunk(content="Here are the universities.", id="message-1"), {},
        )},
    ])
    collected = []
    await _consume(_runtime(), _prepared(agent), asyncio.Event(), collected)
    assert any(e.type == "run.finished" for e in collected)
    assert not any(e.type == "run.error" for e in collected)
    assert "Here are the universities." == "".join(
        e.data["text"] for e in collected if e.type == "message.delta"
    )
    assert not any(e.type == "reasoning.delta" for e in collected)


async def test_blocked_answer_recovers_without_repeating_any_tools() -> None:
    """Only a fresh tool-free model response is allowed after a blocked draft."""
    from langchain_core.messages import ToolMessage

    agent = CountingAgent([
        {"type": "messages", "ns": (), "data": (
            ToolMessage(name="web_search", tool_call_id="s1", content="Official university page found."), {},
        )},
        {"type": "messages", "ns": (), "data": (
            AIMessageChunk(content="## SESSION INTENT\nprivate details"), {},
        )},
    ])
    model = RecoveryModel(["Italy, France, Sweden and Norway each have AI programmes."])
    runtime = _runtime()
    runtime._content_guardrails = RecoveryGuardrails()
    runtime._models = RecoveryModelFactory(model)
    events = []
    await _consume(runtime, _prepared(agent), asyncio.Event(), events)

    assert agent.calls == 1  # the tool graph is never replayed
    assert model.calls == 1
    assert events[-1].type == "run.finished"
    assert events[-1].data["recovery_mode"] == "model_only_no_tools"
    assert "## SESSION INTENT" not in repr(events)
    assert "AI programmes" in "".join(
        e.data["text"] for e in events if e.type == "message.delta"
    )
    assert "Official university page found." in model.messages[-1].content


async def test_recovery_suppresses_second_internal_preamble() -> None:
    agent = CountingAgent([
        {"type": "messages", "ns": (), "data": (
            AIMessageChunk(content="## SESSION INTENT\nprivate details"), {},
        )},
    ])
    model = RecoveryModel(["## INTERNAL SESSION SUMMARY\nprivate details again"])
    runtime = _runtime()
    runtime._content_guardrails = RecoveryGuardrails()
    runtime._models = RecoveryModelFactory(model)
    events = []
    await _consume(runtime, _prepared(agent), asyncio.Event(), events)

    assert agent.calls == model.calls == 1
    assert events[-1].type == "run.error"
    assert events[-1].data["code"] == "internal_context_output_blocked"
    assert all(e.type != "message.delta" for e in events)
    assert "private details" not in repr(events)


async def test_missing_request_does_not_invent_recovery_context() -> None:
    """A HITL resume without a user request must fail safely, not guess."""
    runtime = _runtime()
    runtime._content_guardrails = RecoveryGuardrails()
    runtime._models = RecoveryModelFactory(RecoveryModel(["ignored"]))
    answer = await runtime._recover_safe_answer(
        model_name="test-model", agent_input={"messages": []},
        tool_observations=[], cancel_event=asyncio.Event(),
    )
    assert answer is None
