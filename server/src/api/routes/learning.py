"""Low-cost experience learning and human review APIs."""
from __future__ import annotations

import logging

from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

router = APIRouter(prefix="/learning", tags=["learning"])
logger = logging.getLogger(__name__)


class FeedbackRequest(BaseModel):
    trajectory_id: str = Field(min_length=1)
    rating: Literal["success", "failure"]
    note: str = Field(default="", max_length=1000)


class ReviewRequest(BaseModel):
    decision: Literal["approve", "reject"]


def _learner(request: Request):
    return request.app.state.experience_learning


@router.post("/feedback")
async def feedback(body: FeedbackRequest, request: Request):
    try:
        response = await _learner(request).feedback(**body.model_dump())
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


@router.post("/experiences/{item_id}/review")
async def review_experience(item_id: str, body: ReviewRequest, request: Request):
    try:
        return await _learner(request).review(item_id, decision=body.decision)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/overview")
async def learning_overview(request: Request):
    """Single inexpensive read for the new Skills screen.

    Preserve old records without fetching experiments, mining status, or
    triggering any generation/evaluation work. Candidates created by the old
    engine are reviewable here but never automatically evaluated.
    """
    service = request.app.state.skills_service
    return {
        "items": await _learner(request).list(limit=200),
        "skills": await service.repository.list_registered(),
        "previous_candidates": await service.repository.list_candidates(limit=200),
    }


@router.get("/reflection/status")
async def reflection_status(request: Request, limit: int = 20):
    """Inspect durable review progress without starting or evaluating anything."""
    worker = getattr(request.app.state, "reflection_worker", None)
    if worker is None:
        raise HTTPException(status_code=503, detail="Reflection worker is not available")
    return await worker.status(limit=limit)
