"""Persistent scheduler for autonomous personal-agent jobs."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from server.src.tools.personal.store import PersonalAgentStore

logger = logging.getLogger(__name__)
ScheduleRunner = Callable[[dict[str, Any]], Awaitable[str]]


class SchedulerService:
    def __init__(
        self,
        store: PersonalAgentStore,
        runner: ScheduleRunner,
        *,
        poll_seconds: float = 5.0,
    ) -> None:
        self._store = store
        self._runner = runner
        self._poll_seconds = max(1.0, float(poll_seconds))
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()
        self._active_jobs: set[str] = set()

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stopping.clear()
            self._task = asyncio.create_task(self._loop(), name="trajecta-scheduler")

    async def stop(self) -> None:
        self._stopping.set()
        task = self._task
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self._task = None

    async def _loop(self) -> None:
        while not self._stopping.is_set():
            try:
                jobs = await self._store.due_schedules(limit=20)
                for job in jobs:
                    job_id = str(job["id"])
                    if job_id in self._active_jobs:
                        continue
                    self._active_jobs.add(job_id)
                    asyncio.create_task(self._execute(job), name=f"schedule:{job_id}")
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Scheduled-job poll failed")
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=self._poll_seconds)
            except TimeoutError:
                pass

    async def _execute(self, job: dict[str, Any]) -> None:
        job_id = str(job["id"])
        try:
            try:
                result = await self._runner(job)
            except Exception as exc:
                logger.exception("Scheduled job %s failed", job_id)
                result = f"ERROR: {type(exc).__name__}: {exc}"
            await self._store.mark_schedule_ran(job_id, result=result)
            await self._store.create_notification(
                title=f"Scheduled task: {job.get('name', 'Trajecta job')}",
                body=result[:4_000],
                level="error" if result.startswith("ERROR:") else "info",
                metadata={"schedule_id": job_id, "conversation_id": job.get("conversation_id")},
            )
        finally:
            self._active_jobs.discard(job_id)
