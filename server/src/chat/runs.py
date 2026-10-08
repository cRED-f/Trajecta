from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
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

        # Conversations whose run has been planned but not registered yet.
        # Reservations close the window between preflight checks and
        # registration, so workspace swaps cannot slip in mid-preparation.
        self._reserved: set[str] = set()

        self._lock = asyncio.Lock()

    async def reserve(
        self,
        conversation_id: str,
    ) -> None:
        """Claim a conversation for a run that is about to be prepared."""

        async with self._lock:
            if (
                conversation_id in self._runs
                or conversation_id in self._reserved
            ):
                raise RunAlreadyActive(
                    "A run is already active "
                    "for this conversation"
                )

            self._reserved.add(conversation_id)

    async def release(
        self,
        conversation_id: str,
    ) -> None:
        """Drop a reservation that never became a run."""

        async with self._lock:
            self._reserved.discard(conversation_id)

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

            self._reserved.discard(conversation_id)

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
                self._reserved.discard(conversation_id)

    async def is_active(
        self,
        conversation_id: str,
    ) -> bool:
        """Is a run (or a run being prepared) in flight for this conversation?"""

        async with self._lock:
            return conversation_id in self._runs or conversation_id in self._reserved

    @asynccontextmanager
    async def exclusive(
        self,
        conversation_id: str,
    ) -> AsyncIterator[None]:
        """Hold the registry lock while the caller changes conversation state.

        ``reserve()`` and ``register()`` take the same lock, so a workspace
        swap cannot land between a run's preflight checks and its
        registration. Callers must not await anything that itself needs this
        lock while inside.
        """

        async with self._lock:
            if conversation_id in self._runs or conversation_id in self._reserved:
                raise RunAlreadyActive(
                    "A run is already active for this conversation"
                )
            yield
