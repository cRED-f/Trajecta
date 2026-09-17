"""Task / scheduled-task route handlers.

- GET    /api/v1/tasks/schedules             List scheduled tasks
- POST   /api/v1/tasks/schedules             Create a scheduled task
- GET    /api/v1/tasks/schedules/{job_id}    Fetch one scheduled task
- PATCH  /api/v1/tasks/schedules/{job_id}    Update a scheduled task
- DELETE /api/v1/tasks/schedules/{job_id}    Delete a scheduled task
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request

from pydantic import BaseModel, Field

router = APIRouter(prefix="/tasks", tags=["tasks"])


class ScheduleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    prompt: str = Field(min_length=1)
    schedule_type: Literal["once", "interval", "cron"]
    schedule_expr: str = Field(min_length=1)
    timezone: str = "UTC"
    metadata: dict[str, Any] = Field(default_factory=dict)


class ScheduleUpdate(BaseModel):
    name: str | None = None
    prompt: str | None = None
    schedule_type: Literal["once", "interval", "cron"] | None = None
    schedule_expr: str | None = None
    timezone: str | None = None
    enabled: bool | None = None


def _store(request: Request):
    return request.app.state.personal_tools.store


@router.get("/schedules")
async def list_schedules(request: Request) -> dict:
    schedules = await _store(request).list_schedules()

    return {"schedules": schedules, "count": len(schedules)}


@router.post("/schedules")
async def create_schedule(body: ScheduleCreate, request: Request) -> dict:
    try:
        return await _store(request).create_schedule(
            name=body.name,
            prompt=body.prompt,
            schedule_type=body.schedule_type,
            schedule_expr=body.schedule_expr,
            timezone=body.timezone,
            metadata=body.metadata,
        )
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/schedules/{job_id}")
async def get_schedule(job_id: str, request: Request) -> dict:
    schedule = await _store(request).get_schedule(job_id)

    if schedule is None:
        raise HTTPException(status_code=404, detail="Scheduled task not found")

    return schedule


@router.patch("/schedules/{job_id}")
async def update_schedule(
    job_id: str,
    body: ScheduleUpdate,
    request: Request,
) -> dict:
    try:
        return await _store(request).update_schedule(
            job_id,
            name=body.name,
            prompt=body.prompt,
            schedule_type=body.schedule_type,
            schedule_expr=body.schedule_expr,
            timezone=body.timezone,
            enabled=body.enabled,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/schedules/{job_id}")
async def delete_schedule(job_id: str, request: Request) -> dict:
    deleted = await _store(request).delete_schedule(job_id)

    if not deleted:
        raise HTTPException(status_code=404, detail="Scheduled task not found")

    return {"deleted": True, "id": job_id}