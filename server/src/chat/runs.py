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
    done_event: asyncio.Event
    owner: asyncio.Task | None = None


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
        self._reservation_done: dict[str, asyncio.Event] = {}
        self._cancelled_reservations: set[str] = set()

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
            self._reservation_done[conversation_id] = asyncio.Event()

    async def release(
        self,
        conversation_id: str,
    ) -> None:
        """Drop a reservation that never became a run."""

        async with self._lock:
            self._reserved.discard(conversation_id)
            self._cancelled_reservations.discard(conversation_id)
            event = self._reservation_done.pop(conversation_id, None)
            if event is not None:
                event.set()

    async def register(
        self,
        conversation_id: str,
        run_id: str,
    ) -> asyncio.Event:
        async with self._lock:
            if conversation_id in self._cancelled_reservations:
                self._reserved.discard(conversation_id)
                self._cancelled_reservations.discard(conversation_id)
                event = self._reservation_done.pop(conversation_id, None)
                if event is not None:
                    event.set()
                raise RunAlreadyActive("Run cancelled before generation started")

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
                done_event=asyncio.Event(),
            )

            self._reserved.discard(conversation_id)
            prepared = self._reservation_done.pop(conversation_id, None)
            if prepared is not None:
                prepared.set()

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
                if conversation_id in self._reserved:
                    self._cancelled_reservations.add(conversation_id)
                    return True
                return False

            active.cancel_event.set()

            return True

    async def bind_owner(
        self,
        conversation_id: str,
        run_id: str,
    ) -> bool:
        """Bind a prepared run to its SSE producer, if still active.

        A Stop arriving before streaming starts may retire the prepared run;
        in that case the late producer must not start the model.
        """
        async with self._lock:
            active = self._runs.get(conversation_id)
            if (
                active is None
                or active.run_id != run_id
                or active.cancel_event.is_set()
            ):
                return False
            owner = asyncio.current_task()
            active.owner = owner
            if owner is not None:
                # Safety net: even an unexpected generator failure cannot
                # leave a permanent active-run entry after its task exits.
                owner.add_done_callback(
                    lambda _task: asyncio.create_task(
                        self.unregister(conversation_id, run_id)
                    )
                )
            return True

    async def cancel_and_wait(
        self,
        conversation_id: str,
        *,
        grace_seconds: float = 2.0,
        force_seconds: float = 5.0,
    ) -> bool:
        """Stop and wait for the run to release its conversation slot.

        Stop is a synchronization barrier: the frontend must not start a new
        request until the old stream is terminated. After a brief graceful
        period, cancel the SSE producer task if it has stalled.
        """
        async with self._lock:
            active = self._runs.get(conversation_id)
            if active is None and conversation_id in self._reserved:
                # Stop can arrive while the model is still in preflight.
                self._cancelled_reservations.add(conversation_id)
                reservation = self._reservation_done[conversation_id]
                done_event = None
                owner = None
            elif active is None:
                return False
            else:
                reservation = None
            if active is not None:
                active.cancel_event.set()
            if active is not None and active.owner is None:
                # Prepared but never streamed; a late producer is rejected by
                # bind_owner() and cannot resurrect this run.
                self._runs.pop(conversation_id, None)
                self._reserved.discard(conversation_id)
                active.done_event.set()
                return True
            if active is not None:
                done_event = active.done_event
                owner = active.owner

        if reservation is not None:
            try:
                await asyncio.wait_for(reservation.wait(), timeout=grace_seconds + force_seconds)
                return True
            except TimeoutError:
                return False

        assert done_event is not None and owner is not None
        try:
            await asyncio.wait_for(done_event.wait(), timeout=grace_seconds)
            return True
        except TimeoutError:
            if owner is not asyncio.current_task() and not owner.done():
                owner.cancel()
            try:
                await asyncio.wait_for(done_event.wait(), timeout=force_seconds)
                return True
            except TimeoutError:
                # Do not release the lock while a tool or model is still
                # running; allowing overlapping runs corrupts checkpoints.
                return False

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
                active.done_event.set()

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
