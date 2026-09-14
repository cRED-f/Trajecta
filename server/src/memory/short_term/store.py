"""Short-term memory — current task/thread state (not long-term knowledge).

Thread-scoped: a thread on the LangGraph checkpointer is the short-term memory
(for conversation in flight). Cross-thread durable bits live in SemanticMemory.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, TYPE_CHECKING

from langgraph.checkpoint.base import Checkpoint, CheckpointTuple

if TYPE_CHECKING:
    from server.src.memory.provider import MemoryProvider

_DEFAULT_THREAD_ID = "default"


def new_thread_id() -> str:
    """Fresh thread id for a new task/thread."""
    return uuid.uuid4().hex


def _make_checkpoint(channel_values: dict[str, Any]) -> Checkpoint:
    """Wrap a plain channel-values dict into a valid Checkpoint for `aput`."""
    return Checkpoint(
        v=1,
        id=uuid.uuid4().hex,
        ts=datetime.now(UTC).isoformat(),
        channel_values=channel_values,
        channel_versions={},
        versions_seen={},
        updated_channels=None,
    )


class ShortTermStore:
    """Stores a thread's on-the-fly state, checkpointed in SQLite (checkpointer)."""

    def __init__(self, provider: "MemoryProvider") -> None:
        self._provider = provider
        self._pid = _DEFAULT_THREAD_ID

    @property
    def thread_id(self) -> str:
        return self._pid

    def new_thread(self) -> str:
        """Start a new conversation thread (returns the thread id)."""
        self._pid = new_thread_id()
        return self._pid

    def _checkpointer(self) -> Any:
        cp = self._provider.checkpointer
        if cp is None:
            raise RuntimeError("MemoryProvider not open — call await provider.open() first")
        return cp

    async def save_thread_state(
        self, state: dict[str, Any], *, thread_id: str | None = None, checkpoint_ns: str = ""
    ) -> None:
        """Checkpoint `state` (a channels dict) under the thread id."""
        cp = self._checkpointer()
        tid = thread_id or self._pid
        config = {"configurable": {"thread_id": tid, "checkpoint_ns": checkpoint_ns}}
        checkpoint = _make_checkpoint(state)
        await cp.aput(config, checkpoint, {}, {})

    async def get_thread_state(self, thread_id: str | None = None) -> dict[str, Any] | None:
        """Return the latest checkpoint of a thread (None when thread empty)."""
        cp = self._checkpointer()
        tid = thread_id or self._pid
        config = {"configurable": {"thread_id": tid}}
        tup: CheckpointTuple | None = await cp.aget_tuple(config)
        return tup.checkpoint["channel_values"] if tup else None

    async def list_recent(self, limit: int = 10) -> list[dict[str, Any]]:
        """Latest threads with their most recent channel values (best-effort)."""
        cp = self._checkpointer()
        results: list[dict[str, Any]] = []
        try:
            async for tup in cp.alist(None, limit=limit):
                results.append(
                    {
                        "thread_id": tup.config["configurable"].get("thread_id"),
                        "checkpoint_ns": tup.config["configurable"].get("checkpoint_ns", ""),
                        "created_at": tup.metadata.get("created_at")
                        if (tup.metadata is not None) else None,
                        "values": tup.checkpoint.get("channel_values"),
                    }
                )
        except Exception:  # pragma: no cover — checkpointer listing differs by impl
            pass
        return results