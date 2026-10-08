"""Skill route handlers.

- GET  /api/v1/skills                          List registered skills
- GET  /api/v1/skills/{name}                  Active skill + versions + full bundle
- GET  /api/v1/skills/{name}/versions         Version history
- POST /api/v1/skills/{name}/versions/compare Diff two versions
- GET  /api/v1/skills/{name}/versions/{v}     A specific version
- POST /api/v1/skills/{name}/rollback         Restore a previous version
- POST /api/v1/skills/upgrade                 Evaluate + promote a candidate
- POST /api/v1/skills/experiments             Open an A/B version split
- POST /api/v1/skills/candidates/{id}/experiment  Start a live experiment
- GET  /api/v1/skills/experiments             Live experiments
- GET  /api/v1/skills/experiments/{id}        One experiment
- GET  /api/v1/skills/experiments/{id}/analysis  Evidence + verdict
- POST /api/v1/skills/experiments/{id}/arms   Add a treatment arm
- POST /api/v1/skills/experiments/{id}/stop   Stop and release the arms
- GET  /api/v1/skills/graph                   The dependency graph
- GET  /api/v1/skills/{name}/analytics        Metrics, regressions, experiments
- POST /api/v1/skills/{name}/regression/check Run the regression monitor
- GET  /api/v1/skills/{name}/dependencies     Declared dependencies
- POST /api/v1/skills/{name}/dependencies     Declare a dependency
- DEL  /api/v1/skills/{name}/dependencies/{d} Remove a dependency
- GET  /api/v1/skills/candidates/{id}         Candidate detail + linked evaluation
- POST /api/v1/skills/candidates/{id}/reject  Manually reject a candidate
- GET  /api/v1/skills/evaluations             Evaluation history
- GET  /api/v1/skills/evaluations/{id}        A specific evaluation
- GET  /api/v1/skills/fixtures/{id}           A replay fixture manifest
- GET  /api/v1/skills/learning/status         Automatic learning loop status
- POST /api/v1/skills/learning/run            Manually queue a learning pass
"""

from __future__ import annotations

import asyncio
import json
from contextlib import suppress

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from server.src.api.schemas.skills import (
    DependencyRequest,
    ExperimentArmRequest,
    ExperimentStartRequest,
    ExperimentStopRequest,
    SkillExperimentCreate,
    SkillRegressionCheck,
    SkillRollbackRequest,
    SkillUpgradeRequest,
    SkillVersionCompareRequest,
)
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
    experiments = await service.experiments.list()

    return {
        "skills": registered,
        "candidates": candidates,
        "experiments": experiments,
        "learning": await service.learning.status(),
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
            "experimenting": sum(
                1 for item in candidates if item.get("status") == "experimenting"
            ),
            "running_experiments": sum(
                1 for item in experiments if item.get("status") == "running"
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


@router.post("/candidates/{candidate_id}/evaluate/stream")
async def stream_candidate_evaluation(
    candidate_id: str,
    request: Request,
    upgrade: bool = False,
) -> StreamingResponse:
    """Stream public model output and evaluation stages for ONE candidate."""
    service = _service(request)
    row = await service.repository.get_candidate(candidate_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Candidate not found")
    if row["status"] == "evaluating" or candidate_id in service.active_ui_evaluations:
        raise HTTPException(status_code=409, detail="Candidate already evaluating")

    # Claim this candidate before responding: concurrent button requests
    # must not start overlapping evaluations.
    service.active_ui_evaluations.add(candidate_id)
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=256)

    async def send(message: dict[str, Any]) -> None:
        await queue.put(message)

    async def evaluate_one() -> None:
        try:
            report = await service.evaluator.evaluate(candidate_id, on_event=send)
            if upgrade and report.verdict == "pass":
                await send({"type": "promotion_started"})
                promoted = await service.promoter.promote(
                    candidate_id=candidate_id, evaluation_id=report.id,
                )
                await send({
                    "type": "promoted", "skill": promoted.skill_name,
                    "version": promoted.version,
                })
            await send({"type": "completed", "report": report.model_dump(mode="json")})
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await send({"type": "error", "message": str(exc)})
        finally:
            await send({"type": "_end"})

    async def events():
        job = asyncio.create_task(evaluate_one())
        try:
            while True:
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=10)
                except TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                if message["type"] == "_end":
                    break
                yield (
                    f"event: {message['type']}\n"
                    f"data: {json.dumps(message, ensure_ascii=False)}\n\n"
                )
        finally:
            if not job.done():
                job.cancel()
                with suppress(asyncio.CancelledError):
                    await job
            service.active_ui_evaluations.discard(candidate_id)

    return StreamingResponse(
        events(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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


@router.post("/candidates/{candidate_id}/experiment")
async def experiment_candidate(
    candidate_id: str,
    body: ExperimentStartRequest,
    request: Request,
) -> dict[str, Any]:
    """Stage a verified candidate as the treatment arm of a live experiment."""

    try:
        return await _service(request).experiments.start_candidate(
            candidate_id,
            strategy=body.strategy,
            traffic_percent=body.traffic_percent,
            auto_stop=body.auto_stop,
            auto_promote=body.auto_promote,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/upgrade")
async def upgrade_skill(
    body: SkillUpgradeRequest,
    request: Request,
) -> dict[str, Any]:
    """Evaluate a candidate and promote it only when the evaluation passes."""

    try:
        return await _service(request).upgrade_skill(
            candidate_id=body.candidate_id,
            reason=body.reason,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/experiments", status_code=201)
async def create_skill_experiment(
    body: SkillExperimentCreate,
    request: Request,
) -> dict[str, Any]:
    """Open an A/B split between two versions of a skill."""

    try:
        return await _service(request).create_experiment(
            skill_name=body.skill_name,
            control_version=body.control_version,
            experiment_version=body.experiment_version,
            traffic_percent=body.traffic_percent,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/experiments")
async def list_skill_experiments(
    request: Request,
    skill_name: str | None = None,
) -> list[dict[str, Any]]:
    return await _service(request).experiments.list(skill_name=skill_name)


@router.get("/experiments/{experiment_id}")
async def get_skill_experiment(
    experiment_id: str,
    request: Request,
) -> dict[str, Any]:
    experiment = await _service(request).experiments.get(experiment_id)

    if experiment is None:
        raise HTTPException(status_code=404, detail="Experiment not found")

    return experiment


@router.get("/experiments/{experiment_id}/analysis")
async def analyze_skill_experiment(
    experiment_id: str,
    request: Request,
) -> dict[str, Any]:
    try:
        return await _service(request).experiments.analysis(experiment_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/experiments/{experiment_id}/arms")
async def add_experiment_arm(
    experiment_id: str,
    body: ExperimentArmRequest,
    request: Request,
) -> dict[str, Any]:
    try:
        return await _service(request).experiments.add_candidate_arm(
            experiment_id,
            body.candidate_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/experiments/{experiment_id}/stop")
async def stop_skill_experiment(
    experiment_id: str,
    body: ExperimentStopRequest,
    request: Request,
) -> dict[str, Any]:
    try:
        return await _service(request).experiments.stop(
            experiment_id,
            reason=body.reason or "manual stop",
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/graph")
async def skill_dependency_graph(request: Request) -> dict[str, Any]:
    return await _service(request).dependencies.graph()


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

    versions = await service.versioner.list_versions(skill_name)

    if not versions:
        active = await service.repository.get_active(skill_name)
        if active is None:
            raise HTTPException(status_code=404, detail="Skill not found")

    return versions


@router.post("/{skill_name}/versions/compare")
async def compare_skill_versions(
    skill_name: str,
    body: SkillVersionCompareRequest,
    request: Request,
) -> dict[str, Any]:
    try:
        return await _service(request).versioner.compare_versions(
            skill_name,
            body.from_version,
            body.to_version,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/{skill_name}/analytics")
async def skill_analytics(
    skill_name: str,
    request: Request,
    version: str | None = None,
) -> dict[str, Any]:
    """Aggregate execution metrics, regression history and experiments."""

    return await _service(request).skill_analytics(skill_name, version=version)


@router.get("/{skill_name}/dependencies")
async def list_skill_dependencies(
    skill_name: str,
    request: Request,
) -> list[dict[str, Any]]:
    return await _service(request).dependencies.list_for(skill_name)


@router.post("/{skill_name}/dependencies")
async def add_skill_dependency(
    skill_name: str,
    body: DependencyRequest,
    request: Request,
) -> dict[str, Any]:
    try:
        return await _service(request).dependencies.add(
            skill_name=skill_name,
            depends_on_skill=body.depends_on_skill,
            version_constraint=body.version_constraint,
            required=body.required,
            metadata=body.metadata,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.delete("/{skill_name}/dependencies/{dependency}")
async def delete_skill_dependency(
    skill_name: str,
    dependency: str,
    request: Request,
) -> dict[str, Any]:
    removed = await _service(request).dependencies.remove(skill_name, dependency)

    if not removed:
        raise HTTPException(status_code=404, detail="Dependency not found")

    return {"deleted": True}


@router.post("/{skill_name}/regression/check")
async def check_skill_regression_monitor(
    skill_name: str,
    request: Request,
) -> dict[str, Any]:
    """Compare the live version against the last stable one and roll back."""

    return await _service(request).regression.check(skill_name)


@router.post("/{skill_name}/regressions/check")
async def check_skill_regression(
    skill_name: str,
    body: SkillRegressionCheck,
    request: Request,
) -> dict[str, Any]:
    """Run the automatic rollback policy for one stable/current pair."""

    try:
        return await _service(request).enforce_rollback(
            skill_name,
            stable_version=body.stable_version,
            current_version=body.current_version,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{skill_name}/rollback")
async def rollback_skill(
    skill_name: str,
    body: SkillRollbackRequest,
    request: Request,
) -> dict[str, Any]:
    if body.version is None:
        raise HTTPException(status_code=400, detail="version is required")

    try:
        return await _service(request).promoter.rollback_to_version(
            skill_name=skill_name,
            version=body.version,
            reason=body.reason,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


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