from __future__ import annotations

import asyncio

from dataclasses import dataclass


class RunAlreadyActive(
    RuntimeError
):
    pass


@dataclass(slots=True)
class ActiveRun:
    run_id: str
    cancel_event: asyncio.Event


class ChatRunRegistry:
    """
    One active assistant run per conversation.

    Different conversations may run concurrently.
    """

    def __init__(self) -> None:
        self._runs: dict[
            str,
            ActiveRun,
        ] = {}

        self._lock = asyncio.Lock()

    async def register(
        self,
        conversation_id: str,
        run_id: str,
    ) -> asyncio.Event:
        async with self._lock:
            existing = self._runs.get(
                conversation_id
            )

            if existing is not None:
                raise RunAlreadyActive(
                    "A run is already active "
                    "for this conversation"
                )

            event = asyncio.Event()

            self._runs[
                conversation_id
            ] = ActiveRun(
                run_id=run_id,
                cancel_event=event,
            )

            return event

    async def cancel(
        self,
        conversation_id: str,
    ) -> bool:
        async with self._lock:
            active = self._runs.get(
                conversation_id
            )

            if active is None:
                return False

            active.cancel_event.set()

            return True

    async def unregister(
        self,
        conversation_id: str,
        run_id: str,
    ) -> None:
        async with self._lock:
            active = self._runs.get(
                conversation_id
            )

            if (
                active is not None
                and active.run_id == run_id
            ):
                self._runs.pop(
                    conversation_id,
                    None,
                )
