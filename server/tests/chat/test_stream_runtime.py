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


class CountingAgent(FakeAgent):
    def __init__(self, events):
        super().__init__(events)
        self.calls = 0

    async def astream(self, *args, **kwargs):
        self.calls += 1
        async for event in super().astream(*args, **kwargs):
            yield event


async def test_provider_reasoning_is_not_blocked_by_internal_heading() -> None:
    """An internal-context heading no longer causes a run error."""
    agent = CountingAgent([
        {"type": "messages", "ns": (), "data": (
            AIMessageChunk(content="", id="message-1", additional_kwargs={
                "reasoning_content": "## SESSION INTENT\nProvider reasoning"
            }), {},
        )},
        {"type": "messages", "ns": (), "data": (
            AIMessageChunk(content="Here are the universities.", id="message-1"), {},
        )},
    ])
    collected = []
    await _consume(_runtime(), _prepared(agent), asyncio.Event(), collected)
    assert agent.calls == 1
    assert collected[-1].type == "run.finished"
    assert not any(e.type == "run.error" for e in collected)
    assert "## SESSION INTENT\nProvider reasoning" == "".join(
        e.data["text"] for e in collected if e.type == "reasoning.delta"
    )
    assert "Here are the universities." == "".join(
        e.data["text"] for e in collected if e.type == "message.delta"
    )


async def test_model_output_heading_streams_unchanged_without_recovery() -> None:
    """Even a 1-character-per-token preamble cannot abort the response."""
    from langchain_core.messages import ToolMessage

    response = "## SESSION INTENT\nHere are universities in Italy, France, Sweden and Norway."
    agent = CountingAgent([
        {"type": "messages", "ns": (), "data": (
            ToolMessage(name="web_search", tool_call_id="s1", content="Official university information."), {},
        )},
        *[
            {"type": "messages", "ns": (), "data": (
                AIMessageChunk(content=character, id="answer-1"), {},
            )} for character in response
        ],
        {"type": "updates", "ns": (), "data": {"agent": {}}},
    ])
    collected = []
    await _consume(_runtime(), _prepared(agent), asyncio.Event(), collected)
    assert agent.calls == 1
    assert collected[-1].type == "run.finished"
    assert "recovery_mode" not in collected[-1].data
    assert not any(e.type == "run.error" for e in collected)
    deltas = [e.data["text"] for e in collected if e.type == "message.delta"]
    assert deltas == list(response)
    assert "".join(deltas) == response
    assert any(e.type == "tool.result" for e in collected)
    assert [e.type for e in collected].index("message.delta") < [e.type for e in collected].index("agent.step")


async def test_short_heading_is_not_quarantined_or_modified() -> None:
    text = "## Summary"
    agent = CountingAgent([
        {"type": "messages", "ns": (), "data": (AIMessageChunk(content="#"), {})},
        {"type": "messages", "ns": (), "data": (AIMessageChunk(content="# Summary"), {})},
    ])
    collected = []
    await _consume(_runtime(), _prepared(agent), asyncio.Event(), collected)
    assert [e.data["text"] for e in collected if e.type == "message.delta"] == ["#", "# Summary"]
    assert collected[-1].type == "run.finished"
