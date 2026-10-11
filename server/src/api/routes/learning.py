"""Automatic task learning, user feedback and diagnostics APIs."""
from __future__ import annotations

import logging
import re

from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

router = APIRouter(prefix="/learning", tags=["learning"])
logger = logging.getLogger(__name__)


class FeedbackRequest(BaseModel):
    trajectory_id: str = Field(min_length=1)
    rating: Literal["success", "failure"]
    note: str = Field(default="", max_length=1000)


def _learner(request: Request):
    return request.app.state.experience_learning


@router.post("/feedback")
async def feedback(body: FeedbackRequest, request: Request):
    try:
        response = await _learner(request).feedback(**body.model_dump())
        # The feedback rating belongs to the original trajectory and episode;
        # preserve independent-verification status and update the vector index.
        provider = getattr(request.app.state, "memory_provider", None)
        if provider is not None:
            try:
                await provider.episodic.sync_feedback(body.trajectory_id, body.rating)
            except Exception:
                logger.warning("episode feedback synchronization failed", exc_info=True)
        # Feedback is committed before any analytics or model work.
        # Queueing must not depend on downstream skill metrics succeeding.
        worker = getattr(request.app.state, "reflection_worker", None)
        if worker is not None:
            try:
                await worker.enqueue(body.trajectory_id, reason="feedback")
            except Exception:
                logger.warning("feedback reflection enqueue failed", exc_info=True)
        skills = request.app.state.skills_service
        trajectory = await skills.trajectories.get(body.trajectory_id)
        if trajectory is not None:
            await skills.execution.attribute(body.trajectory_id)
            await skills.complete_runtime_trajectory(
                body.trajectory_id,
                success=body.rating == "success",
                metrics=(trajectory.get("metadata") or {}).get("run_metrics") or {},
            )
        return response
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/experiences")
async def experiences(request: Request, status: str | None = None, limit: int = 100):
    try:
        items = await _learner(request).list(status=status, limit=limit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"items": items, "counts": {
        key: sum(item["status"] == key for item in items)
        for key in ("active", "needs_review", "rejected")
    }}


@router.get("/overview")
async def learning_overview(request: Request):
    """Single inexpensive read for the new Skills screen.

    Preserve old records without initiating generation/evaluation work.
    Inactive candidates never change tool permissions.
    """
    service = request.app.state.skills_service
    return {
        "items": await _learner(request).list(limit=200),
        "skills": await service.repository.list_registered(),
        "previous_candidates": await service.repository.list_candidates(limit=200),
    }


class ReflectionSettingsUpdate(BaseModel):
    enabled: bool | None = None
    # A qualified provider/model, or null to follow the global default.
    model: str | None = Field(default=None, max_length=200)
    max_daily_reviews: int | None = Field(default=None, ge=0, le=1000)
    max_output_tokens: int | None = Field(default=None, ge=100, le=2000)
    timeout_seconds: float | None = Field(default=None, ge=5, le=180)


@router.get("/reflection/settings")
async def reflection_settings(request: Request):
    worker = getattr(request.app.state, "reflection_worker", None)
    if worker is None:
        raise HTTPException(503, "Reflection worker is not available")
    return await worker.get_config()


@router.patch("/reflection/settings")
async def update_reflection_settings(body: ReflectionSettingsUpdate, request: Request):
    worker = getattr(request.app.state, "reflection_worker", None)
    if worker is None:
        raise HTTPException(503, "Reflection worker is not available")
    patch = body.model_dump(exclude_unset=True)
    if any(value is None for key, value in patch.items() if key != "model"):
        raise HTTPException(422, "Review limits and enabled status cannot be null")
    if "model" in patch:
        model = patch["model"]
        if model is not None and not re.fullmatch(r"(?:[A-Za-z0-9._:-]+|[A-Za-z0-9._-]+/[A-Za-z0-9._:/-]+)", model):
            raise HTTPException(400, "Choose a Bifrost model id or leave blank for the default")
    return await worker.update_config(patch)


@router.get("/reflection/status")
async def reflection_status(request: Request, limit: int = 20):
    """Inspect durable review progress without starting or evaluating anything."""
    worker = getattr(request.app.state, "reflection_worker", None)
    if worker is None:
        raise HTTPException(status_code=503, detail="Reflection worker is not available")
    return await worker.status(limit=limit)



@router.get("/tasks")
async def list_learning_tasks(request: Request, limit: int = 50, workspace_path: str | None = None):
    """Task lifecycle and evidence, no approval decisions required."""
    from server.src.memory.episodic.store import EpisodicMemory
    store = request.app.state.skills_service.trajectories
    scope = EpisodicMemory.workspace_scope(workspace_path)
    return {"items": await store.tracker.list(scope=scope,limit=limit)}
