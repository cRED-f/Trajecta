"""Bounded, candidate-scoped in-memory streams for background evaluations.

Only public evaluation events are relayed. No hidden reasoning, raw tool
arguments, secrets or private tool results are added by this module.
The recent-event buffer allows the Skills UI to attach after a run starts.
"""
from __future__ import annotations

import asyncio
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from typing import Any, AsyncIterator


@dataclass
class _Session:
    events: deque[tuple[int, dict[str, Any]]] = field(
        default_factory=lambda: deque(maxlen=4000)
    )
    sequence: int = 0
    finished: bool = False
    changed: asyncio.Condition = field(default_factory=asyncio.Condition)


class BackgroundEvaluationStreams:
    """One replayable public event stream per candidate, with bounded memory."""

    def __init__(self) -> None:
        self._sessions: OrderedDict[str, _Session] = OrderedDict()

    def begin(self, candidate_id: str) -> None:
        self._sessions[candidate_id] = _Session()
        self._sessions.move_to_end(candidate_id)
        # Do not evict active sessions merely to enforce the retention limit.
        for key in list(self._sessions):
            if len(self._sessions) <= 30:
                break
            if self._sessions[key].finished:
                del self._sessions[key]

    def exists(self, candidate_id: str) -> bool:
        return candidate_id in self._sessions

    async def publish(self, candidate_id: str, event: dict[str, Any]) -> None:
        session = self._sessions.get(candidate_id)
        if session is None or session.finished:
            return

        # Keep only the text already explicitly emitted as visible model output.
        # Avoid accidentally storing oversized events indefinitely.
        message = dict(event)
        if message.get("type") == "model_delta":
            message["text"] = str(message.get("text") or "")[:1024]

        async with session.changed:
            session.sequence += 1
            session.events.append((session.sequence, message))
            if message.get("type") in {"completed", "error"}:
                session.finished = True
            session.changed.notify_all()

    async def follow(self, candidate_id: str) -> AsyncIterator[dict[str, Any] | None]:
        session = self._sessions.get(candidate_id)
        if session is None:
            raise KeyError(candidate_id)

        cursor = 0
        while True:
            async with session.changed:
                if session.events and cursor < session.events[0][0] - 1:
                    cursor = session.events[0][0] - 1
                    yield_item: dict[str, Any] | None = {
                        "type": "history_truncated",
                        "message": "Earlier live output is no longer buffered.",
                    }
                else:
                    pending = [
                        (seq, message)
                        for seq, message in session.events
                        if seq > cursor
                    ]
                    if pending:
                        cursor, message = pending[0]
                        yield_item = {**message, "seq": cursor}
                    elif session.finished:
                        return
                    else:
                        try:
                            await asyncio.wait_for(session.changed.wait(), timeout=12)
                            continue
                        except TimeoutError:
                            yield_item = None  # SSE keepalive
            yield yield_item
