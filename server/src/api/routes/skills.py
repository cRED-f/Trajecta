"""Skill route handlers.

- GET  /api/v1/skills                          List registered skills
- GET  /api/v1/skills/{name}                  Active skill + versions + full bundle
- GET  /api/v1/skills/{name}/versions         Version history
- GET  /api/v1/skills/{name}/versions/{v}     A specific version
- GET  /api/v1/skills/candidates/{id}         Candidate detail + linked evaluation
- POST /api/v1/skills/candidates/{id}/reject  Manually reject a candidate
- GET  /api/v1/skills/evaluations             Evaluation history
- GET  /api/v1/skills/evaluations/{id}        A specific evaluation
- GET  /api/v1/skills/fixtures/{id}           A replay fixture manifest
- GET  /api/v1/skills/learning/status         Automatic learning loop status
- POST /api/v1/skills/learning/run            Manually queue a learning pass
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from server.src.skills.service import SkillsService

router = APIRouter(prefix="/skills", tags=["skills"])


def _service(request: Request) -> SkillsService:
    return request.app.state.skills_service


class RejectSkillRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)


class SkillEnabledUpdate(BaseModel):
    enabled: bool


class SkillLearningRunRequest(BaseModel):
    force: bool = True


@router.get("")
async def list_skills(request: Request) -> dict[str, Any]:
    service = _service(request)

    registered = await service.repository.list_registered()
    candidates = await service.repository.list_candidates(limit=200)

    return {
        "skills": registered,
        "candidates": candidates,
        "summary": {
            "active": sum(
                1 for item in registered if item.get("status") == "active"
            ),
            "disabled": sum(
                1 for item in registered if item.get("status") == "disabled"
            ),
            "candidate": sum(
                1 for item in candidates if item.get("status") == "candidate"
            ),
            "evaluating": sum(
                1 for item in candidates if item.get("status") == "evaluating"
            ),
        },
    }


@router.post("/candidates/{candidate_id}/evaluate")
async def evaluate_candidate(
    candidate_id: str,
    request: Request,
) -> dict[str, Any]:
    try:
        report = await _service(request).evaluator.evaluate(candidate_id)
        return report.model_dump(mode="json")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/candidates/{candidate_id}/promote")
async def promote_candidate(
    candidate_id: str,
    request: Request,
) -> dict[str, Any]:
    try:
        result = await _service(request).promoter.promote(candidate_id=candidate_id)
        return {
            "skill_name": result.skill_name,
            "version": result.version,
            "version_id": result.version_id,
            "previous_version": result.previous_version,
        }
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/learning/status")
async def skill_learning_status(request: Request) -> dict[str, Any]:
    return await _service(request).learning.status()


@router.post("/learning/run", status_code=202)
async def queue_skill_learning_run(
    request: Request,
    body: SkillLearningRunRequest,
) -> dict[str, Any]:
    service = _service(request)
    queued = service.learning.request_run(force=body.force)

    return {
        "queued": queued,
        "force": body.force,
        "status": await service.learning.status(),
    }


@router.patch("/{skill_name}/enabled")
async def set_skill_enabled(
    skill_name: str,
    body: SkillEnabledUpdate,
    request: Request,
) -> dict[str, Any]:
    service = _service(request)

    active = await service.repository.get_active(skill_name)

    if active is None:
        raise HTTPException(status_code=404, detail="Skill not found")

    if not body.enabled:
        await request.app.state.memory_provider.procedural.adelete(skill_name)
        await service.repository.set_registry_status(skill_name, "disabled")
        return {"name": skill_name, "enabled": False}

    version = str(active["version"])

    skill = await service.repository.get_version_skill(skill_name, version)

    if skill is None:
        raise HTTPException(
            status_code=409,
            detail="Active skill version snapshot is missing",
        )

    await request.app.state.memory_provider.procedural.apromote_bundle(
        skill.name,
        skill.bundle_files(),
    )
    await service.repository.set_registry_status(skill_name, "active")

    return {"name": skill_name, "enabled": True}


@router.get("/candidates/{candidate_id}")
async def get_candidate(
    candidate_id: str,
    request: Request,
) -> dict[str, Any]:
    service = _service(request)

    candidate = await service.repository.get_candidate(candidate_id)
    skill = await service.repository.get_candidate_skill(candidate_id)

    if candidate is None or skill is None:
        raise HTTPException(status_code=404, detail="Skill candidate not found")

    evaluation = None
    evaluation_id = candidate.get("metadata", {}).get("evaluation_id")

    if evaluation_id:
        evaluation = await service.repository.get_evaluation(str(evaluation_id))

    return {
        "candidate": candidate,
        "skill": skill.model_dump(mode="json"),
        "evaluation": evaluation,
    }


@router.post("/candidates/{candidate_id}/reject")
async def reject_candidate(
    candidate_id: str,
    request: Request,
    body: RejectSkillRequest,
) -> dict[str, Any]:
    try:
        return await _service(request).reject_candidate(
            candidate_id,
            reason=body.reason,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/evaluations")
async def list_evaluations(
    request: Request,
    skill_name: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    return await _service(request).repository.list_evaluations(
        skill_name=skill_name,
        limit=limit,
    )


@router.get("/evaluations/{evaluation_id}")
async def get_evaluation(
    evaluation_id: str,
    request: Request,
) -> dict[str, Any]:
    evaluation = await _service(request).repository.get_evaluation(evaluation_id)

    if evaluation is None:
        raise HTTPException(status_code=404, detail="Evaluation not found")

    return evaluation


@router.get("/fixtures/{fixture_id}")
async def get_replay_fixture(
    fixture_id: str,
    request: Request,
) -> dict[str, Any]:
    fixture = await _service(request).replay_fixtures.get(fixture_id)

    if fixture is None:
        raise HTTPException(status_code=404, detail="Replay fixture not found")

    return fixture.model_dump(mode="json")


@router.get("/{skill_name}/versions")
async def list_skill_versions(
    skill_name: str,
    request: Request,
) -> list[dict[str, Any]]:
    service = _service(request)

    versions = await service.repository.list_versions(skill_name)

    if not versions:
        active = await service.repository.get_active(skill_name)
        if active is None:
            raise HTTPException(status_code=404, detail="Skill not found")

    return versions


@router.get("/{skill_name}/versions/{version}")
async def get_skill_version(
    skill_name: str,
    version: str,
    request: Request,
) -> dict[str, Any]:
    service = _service(request)

    record = await service.repository.get_version(skill_name, version)
    skill = await service.repository.get_version_skill(skill_name, version)

    if record is None or skill is None:
        raise HTTPException(status_code=404, detail="Skill version not found")

    return {
        "version": record,
        "skill": skill.model_dump(mode="json"),
    }


@router.get("/{skill_name}")
async def get_skill(
    skill_name: str,
    request: Request,
) -> dict[str, Any]:
    service = _service(request)

    active = await service.repository.get_active(skill_name)
    versions = await service.repository.list_versions(skill_name)

    if active is None and not versions:
        raise HTTPException(status_code=404, detail="Skill not found")

    memory_provider = request.app.state.memory_provider
    bundle = await memory_provider.procedural.aload_bundle(skill_name)

    return {
        "active": active,
        "versions": versions,
        "bundle": bundle,
    }