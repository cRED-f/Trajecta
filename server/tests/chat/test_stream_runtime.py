"""Regression tests for the streaming run loop in chat.runtime.

Covers two fixes:
1. Stop must cancel a run that is blocked on a silent model stream
   (the old loop only polled cancel_event when a chunk arrived).
2. Long answers stream in validated batches before the model step
   completes, instead of being quarantined until the updates event.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from langchain_core.messages import AIMessageChunk

from server.src.chat.models import Conversation
from server.src.chat.runtime import (
    OUTPUT_BATCH_CHARS,
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


class StubGuardrails:
    def __init__(self) -> None:
        self.validated: list[str] = []

    async def validate_assistant_output(
        self,
        text: str,
        *,
        system_prompt: str,
    ) -> list[Any]:
        self.validated.append(text)
        return []


class StubMemory:
    checkpointer = None


def _runtime(guardrails: StubGuardrails | None = None) -> DeepAgentRuntime:
    runtime = DeepAgentRuntime.__new__(DeepAgentRuntime)
    runtime._content_guardrails = guardrails or StubGuardrails()
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
        system_prompt="SYSTEM",
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


async def test_long_answer_streams_deltas_before_model_step_completes() -> None:
    guardrails = StubGuardrails()
    runtime = _runtime(guardrails)

    text = "x" * (OUTPUT_BATCH_CHARS * 2 + 512)
    events: list[dict[str, Any]] = [
        {
            "type": "messages",
            "ns": (),
            "data": (AIMessageChunk(content=text[offset : offset + 700]), {}),
        }
        for offset in range(0, len(text), 700)
    ]
    # Model step completes: the middleware accepted the full response.
    events.append({"type": "updates", "ns": (), "data": {"agent": {}}})

    collected: list[Any] = []
    await asyncio.wait_for(
        _consume(runtime, _prepared(FakeAgent(events)), asyncio.Event(), collected),
        timeout=5.0,
    )

    types = [event.type for event in collected]
    delta_indexes = [
        index for index, kind in enumerate(types) if kind == "message.delta"
    ]
    finished = types.index("run.finished")

    # At least one batch was displayed while the model was still
    # generating -- before the updates event -- not only at the end.
    first_step = types.index("agent.step")
    assert delta_indexes, "expected at least one streamed message.delta"
    assert delta_indexes[0] < first_step
    assert all(index < finished for index in delta_indexes)

    assembled = "".join(
        event.data["text"] for event in collected if event.type == "message.delta"
    )
    assert assembled == text

    # Every batch (with its guard tail) passed validate_assistant_output
    # before it was shown, and the flush re-checked the end of the answer.
    assert len(guardrails.validated) >= 2
    assert guardrails.validated[-1].endswith(text[-512:])
