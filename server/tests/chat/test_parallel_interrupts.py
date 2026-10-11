"""Regression tests for parallel Deep Agents HITL approval handling."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from server.src.chat.models import Conversation
from server.src.chat.runtime import DeepAgentRuntime, PreparedAgentRun
from server.src.chat.service import InvalidMessageOperation, _build_resume_payload


def _group(interrupt_id: str | None, *names: str) -> dict[str, Any]:
    return {
        **({"id": interrupt_id} if interrupt_id else {}),
        "action_requests": [{"name": name, "args": {}} for name in names],
        "review_configs": [
            {"action_name": name, "allowed_decisions": ["approve", "reject"]}
            for name in names
        ],
    }


def test_parallel_resume_maps_decisions_to_their_own_interrupt_ids():
    interrupt = {"interrupts": [_group("branch-1", "write_file", "execute"), _group("branch-2", "web_extract")]}
    decisions = [
        {"type": "approve"},
        {"type": "reject", "message": "No execution"},
        {"type": "approve"},
    ]
    assert _build_resume_payload(interrupt, decisions) == {
        "branch-1": {"decisions": decisions[:2]},
        "branch-2": {"decisions": decisions[2:]},
    }


def test_single_interrupt_retains_compatibility_with_old_checkpoints():
    decisions = [{"type": "approve"}]
    assert _build_resume_payload(_group(None, "write_file"), decisions) == {"decisions": decisions}
    assert _build_resume_payload(_group("one-id", "write_file"), decisions) == {"decisions": decisions}


@pytest.mark.parametrize("groups", [
    [_group(None, "write_file"), _group("b", "execute")],
    [_group("a", "write_file"), _group("a", "execute")],
])
def test_parallel_resume_rejects_missing_or_duplicate_ids(groups):
    with pytest.raises(InvalidMessageOperation):
        _build_resume_payload({"interrupts": groups}, [{"type": "approve"}, {"type": "approve"}])


def test_decision_validation_matches_each_interrupts_review_configuration():
    first = _group("a", "write_file")
    second = _group("b", "execute")
    second["review_configs"][0]["allowed_decisions"] = ["reject"]
    with pytest.raises(InvalidMessageOperation, match="not allowed"):
        _build_resume_payload({"interrupts": [first, second]}, [{"type": "approve"}, {"type": "approve"}])


def test_parallel_resume_rejects_partial_decision_sets():
    with pytest.raises(InvalidMessageOperation, match="Expected 2"):
        _build_resume_payload({"interrupts": [_group("a", "write_file"), _group("b", "execute")]}, [{"type": "approve"}])


class FakeAgent:
    def __init__(self, events: list[dict[str, Any]]) -> None:
        self.events = events
        self.input: Any = None

    async def astream(self, agent_input: Any, **_kwargs: Any):
        self.input = agent_input
        for event in self.events:
            yield event


def _runtime() -> DeepAgentRuntime:
    runtime = DeepAgentRuntime.__new__(DeepAgentRuntime)
    async def latest(_thread_id: str) -> str:
        return "checkpoint-123"
    runtime.latest_checkpoint_id = latest  # type: ignore[method-assign]
    return runtime


def _conversation() -> Conversation:
    now = datetime.now(timezone.utc)
    return Conversation(
        id="c", thread_id="thread", title="t", model="m",
        archived=False, created_at=now, updated_at=now,
    )


def _prepared(agent: FakeAgent) -> PreparedAgentRun:
    return PreparedAgentRun(
        agent=agent,
        config={"configurable": {"thread_id": "thread"}},
        model_name="model", mcp_tool_count=0, thread_id="thread",
    )


@pytest.mark.asyncio
async def test_runtime_collects_all_parallel_interrupt_chunks():
    first = SimpleNamespace(id="interrupt-a", value=_group(None, "write_file"))
    second = SimpleNamespace(id="interrupt-b", value=_group(None, "execute"))
    agent = FakeAgent([
        {"type": "updates", "ns": ("subagent-a",), "data": {"__interrupt__": [first]}},
        {"type": "updates", "ns": ("subagent-b",), "data": {"__interrupt__": [second]}},
        # The parent may repeat an interrupt already seen in a child update.
        {"type": "updates", "ns": (), "data": {"__interrupt__": [first]}},
    ])
    events = [event async for event in _runtime()._stream_input(
        prepared=_prepared(agent), conversation=_conversation(),
        agent_input={"messages": []}, cancel_event=asyncio.Event(), resumed=False,
    )]
    interrupted = [event for event in events if event.type == "run.interrupted"]
    assert len(interrupted) == 1
    assert "run.finished" not in [event.type for event in events]
    assert interrupted[0].data["interrupt"] == {
        "interrupts": [
            {**_group(None, "write_file"), "id": "interrupt-a"},
            {**_group(None, "execute"), "id": "interrupt-b"},
        ]
    }
    assert interrupted[0].data["checkpoint_id"] == "checkpoint-123"


@pytest.mark.asyncio
async def test_runtime_resumes_by_id_without_replaying_full_chat():
    agent = FakeAgent([])
    runtime = _runtime()
    resume_payload = {"a": {"decisions": [{"type": "approve"}]}, "b": {"decisions": [{"type": "approve"}]}}
    events = [event async for event in runtime.stream_resume(
        prepared=_prepared(agent), conversation=_conversation(),
        resume_payload=resume_payload, cancel_event=asyncio.Event(),
    )]
    assert agent.input.resume == resume_payload
    assert events[-1].type == "run.finished"
