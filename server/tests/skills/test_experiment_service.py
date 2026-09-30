"""Live experiment lifecycle: arming, sticky assignment, verdicts, attribution."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from server.src.config import Settings
from server.src.memory.storage.sqlite import SQLiteDatabase
from server.src.skills.analytics import (
    SkillAnalyticsService,
    SkillExecutionAttributor,
    SkillMetricsCollector,
)
from server.src.skills.experiments import SkillExperimentService
from server.src.skills.repository import SkillRepository
from server.src.skills.representation.skill import Skill, SkillStatus, SkillWorkflow
from server.src.skills.trajectory_store import TrajectoryStore


def _skill(name: str, version: str, trigger: str) -> Skill:
    return Skill(
        name=name,
        version=version,
        description=f"handles {trigger}",
        instructions="Do the thing.",
        workflow=SkillWorkflow(trigger=trigger),
    )


class _FakePromoter:
    """Stages the next version without running the evaluator."""

    def __init__(self, repository: SkillRepository) -> None:
        self._repository = repository
        self.count = 0

    async def stage(self, *, candidate_id: str, evaluation_id: str | None = None):
        self.count += 1
        version = f"1.{self.count}.0"
        skill = await self._repository.get_candidate_skill(candidate_id)
        assert skill is not None
        skill = skill.model_copy(update={"version": version})
        row = await self._repository.add_version(
            skill,
            source_candidate_id=candidate_id,
            source_evaluation_id=evaluation_id,
            status="staged",
        )
        return SimpleNamespace(
            skill_name="demo",
            version=version,
            version_id=str(row["id"]),
            previous_version=None,
        )


class _FakeVersioner:
    def __init__(self) -> None:
        self.activated: list[tuple[str, str]] = []

    async def activate_version(self, skill_name: str, version: str, *, metadata=None):
        self.activated.append((skill_name, version))
        return SimpleNamespace(
            skill_name=skill_name,
            version=version,
            version_id="vid",
            previous_version=None,
        )


async def _db(tmp_path: Any) -> SQLiteDatabase:
    db = SQLiteDatabase(tmp_path / "experiments.db")
    await db.open()
    return db


async def _harness(db: SQLiteDatabase) -> tuple[SkillExperimentService, SkillRepository,
                                               SkillMetricsCollector, _FakeVersioner]:
    settings = Settings.load()
    repository = SkillRepository(db)
    analytics = SkillAnalyticsService(db)
    metrics = SkillMetricsCollector(db)

    active = _skill("demo", "1.0.0", "deploying the app")
    version = await repository.add_version(
        active,
        source_candidate_id=None,
        source_evaluation_id=None,
        status="active",
    )
    await repository.set_active(active, version_id=version["id"])

    candidate = await repository.create_candidate(
        _skill("demo", "1.1.0", "deploying the app")
    )
    await repository.update_candidate(candidate["id"], status=SkillStatus.VERIFIED)

    versioner = _FakeVersioner()
    service = SkillExperimentService(
        db=db,
        settings=settings,
        repository=repository,
        promoter=_FakePromoter(repository),
        versioner=versioner,
        analytics=analytics,
        trajectories=TrajectoryStore(db),
    )
    return service, repository, metrics, versioner


async def _candidate_id(repository: SkillRepository) -> str:
    rows = await repository.list_candidates(limit=10)
    return str(rows[0]["id"])


async def _sample(
    metrics: SkillMetricsCollector,
    *,
    version: str,
    experiment_id: str,
    arm_kind: str,
    successes: int,
    total: int,
) -> None:
    for index in range(total):
        await metrics.record(
            skill_name="demo",
            skill_version=version,
            trajectory_id=f"{arm_kind}-{index}",
            success=index < successes,
            latency_ms=100.0,
            tokens=10,
            tool_failures=0,
            experiment_id=experiment_id,
            arm_kind=arm_kind,
            unit_id=f"{arm_kind}-unit-{index}",
        )


# --------------------------------------------------------------------------
# arming
# --------------------------------------------------------------------------


async def test_start_candidate_arms_control_and_treatment(
    tmp_path: Any,
) -> None:
    db = await _db(tmp_path)

    try:
        service, repository, _metrics, _versioner = await _harness(db)
        candidate_id = await _candidate_id(repository)

        experiment = await service.start_candidate(candidate_id)

        assert experiment["strategy"] == "ab"
        assert experiment["control_version"] == "1.0.0"
        assert experiment["status"] == "running"

        arms = {arm["version"]: arm["is_control"] for arm in experiment["arms"]}
        assert arms["1.0.0"] == 1
        assert "1.1.0" in arms
        assert arms["1.1.0"] == 0

        staged = await repository.get_candidate(candidate_id)
        assert staged is not None
        assert staged["status"] == SkillStatus.EXPERIMENTING

        with pytest.raises(ValueError, match="already has a running experiment"):
            await service.start_candidate(candidate_id)

    finally:
        await db.close()


# --------------------------------------------------------------------------
# assignment
# --------------------------------------------------------------------------


async def test_prepare_task_is_sticky_and_silent_on_control(
    tmp_path: Any,
) -> None:
    db = await _db(tmp_path)

    try:
        service, repository, _metrics, _versioner = await _harness(db)
        candidate_id = await _candidate_id(repository)
        experiment = await service.start_candidate(candidate_id)

        task = "please deploy the app to staging"
        first = await service.prepare_task(task_text=task, unit_id="thread-a")
        second = await service.prepare_task(task_text=task, unit_id="thread-a")

        assert [item.version for item in first] == [item.version for item in second]
        assert first[0].experiment_id == experiment["id"]
        assert first[0].arm_kind in {"control", "treatment"}

        # An irrelevant task must not drag a skill variant into the run.
        assert await service.prepare_task(
            task_text="summarise this quarterly earnings pdf", unit_id="thread-b"
        ) == []

        if first[0].arm_kind == "control":
            # The control arm runs exactly what is already materialized.
            assert first[0].prompt_override is None
        else:
            assert first[0].prompt_override is not None
            assert "trajecta-skill-experiment" in first[0].prompt_override
            assert first[0].version in first[0].prompt_override

    finally:
        await db.close()


async def test_bind_trajectory_records_assignments_as_a_step(
    tmp_path: Any,
) -> None:
    db = await _db(tmp_path)

    try:
        service, repository, _metrics, _versioner = await _harness(db)
        candidate_id = await _candidate_id(repository)
        await service.start_candidate(candidate_id)

        store = TrajectoryStore(db)
        _, trajectory_id = await store.begin(goal="deploy", thread_id="thread-a")
        assignments = await service.prepare_task(
            task_text="please deploy the app to staging", unit_id="thread-a"
        )
        await service.bind_trajectory(trajectory_id, assignments)

        trajectory = await store.get(trajectory_id)
        assert trajectory is not None
        events = [step["type"] for step in trajectory["steps"]]
        assert "skill.assignments" in events

        payloads = [
            step["data"]["assignments"]
            for step in trajectory["steps"]
            if step["type"] == "skill.assignments"
        ]
        # The experimental prompt belongs to the agent, not to event metadata.
        assert payloads[0][0]["skill_name"] == "demo"
        assert "prompt_override" not in payloads[0][0]

    finally:
        await db.close()


# --------------------------------------------------------------------------
# verdicts
# --------------------------------------------------------------------------


async def test_clear_winner_stops_the_experiment_and_activates(
    tmp_path: Any,
) -> None:
    db = await _db(tmp_path)

    try:
        service, repository, metrics, versioner = await _harness(db)
        candidate_id = await _candidate_id(repository)
        experiment = await service.start_candidate(candidate_id)

        await _sample(
            metrics,
            version=str(experiment["experiment_version"]),
            experiment_id=str(experiment["id"]),
            arm_kind="treatment",
            successes=90,
            total=100,
        )
        await _sample(
            metrics,
            version=str(experiment["control_version"]),
            experiment_id=str(experiment["id"]),
            arm_kind="control",
            successes=10,
            total=100,
        )

        verdict = await service.analysis(str(experiment["id"]))

        assert verdict["decision"] == "treatment_wins"
        assert verdict["winner_version"] == experiment["experiment_version"]
        assert verdict["auto_stopped"] is True
        assert verdict["auto_promoted"] is True
        assert verdict["status"] == "stopped"
        assert versioner.activated == [("demo", experiment["experiment_version"])]

        staged = await repository.get_candidate(candidate_id)
        assert staged is not None
        assert staged["status"] == SkillStatus.VERIFIED

    finally:
        await db.close()


async def test_harmful_treatment_keeps_the_control(tmp_path: Any) -> None:
    db = await _db(tmp_path)

    try:
        service, repository, metrics, versioner = await _harness(db)
        candidate_id = await _candidate_id(repository)
        experiment = await service.start_candidate(candidate_id)

        await _sample(
            metrics,
            version=str(experiment["experiment_version"]),
            experiment_id=str(experiment["id"]),
            arm_kind="treatment",
            successes=10,
            total=100,
        )
        await _sample(
            metrics,
            version=str(experiment["control_version"]),
            experiment_id=str(experiment["id"]),
            arm_kind="control",
            successes=90,
            total=100,
        )

        verdict = await service.analysis(str(experiment["id"]))

        assert verdict["decision"] == "control_wins"
        assert verdict["winner_version"] == experiment["control_version"]
        assert verdict["auto_promoted"] is False
        assert versioner.activated == []

    finally:
        await db.close()


async def test_budget_without_evidence_is_inconclusive(tmp_path: Any) -> None:
    db = await _db(tmp_path)

    try:
        service, repository, metrics, versioner = await _harness(db)
        candidate_id = await _candidate_id(repository)
        experiment = await service.start_candidate(candidate_id)

        # Equal arms, exactly at max_samples_total (200 by default).
        await _sample(
            metrics,
            version=str(experiment["experiment_version"]),
            experiment_id=str(experiment["id"]),
            arm_kind="treatment",
            successes=50,
            total=100,
        )
        await _sample(
            metrics,
            version=str(experiment["control_version"]),
            experiment_id=str(experiment["id"]),
            arm_kind="control",
            successes=50,
            total=100,
        )

        verdict = await service.analysis(str(experiment["id"]))

        assert verdict["decision"] == "inconclusive"
        assert verdict["winner_version"] is None
        assert verdict["auto_stopped"] is True
        assert versioner.activated == []

    finally:
        await db.close()


async def test_stopped_experiment_releases_the_candidate(tmp_path: Any) -> None:
    db = await _db(tmp_path)

    try:
        service, repository, _metrics, _versioner = await _harness(db)
        candidate_id = await _candidate_id(repository)
        experiment = await service.start_candidate(candidate_id)
        assert (await repository.get_candidate(candidate_id))["status"] == (
            SkillStatus.EXPERIMENTING
        )

        stopped = await service.stop(str(experiment["id"]), reason="not working")

        assert stopped["status"] == "stopped"
        assert stopped["reason"] == "not working"

        released = await repository.get_candidate(candidate_id)
        assert released is not None
        assert released["status"] == SkillStatus.VERIFIED

        # A released candidate can be armed again.
        again = await service.start_candidate(candidate_id)
        assert again["status"] == "running"

    finally:
        await db.close()


# --------------------------------------------------------------------------
# attribution
# --------------------------------------------------------------------------


async def test_attribution_tags_rows_with_the_assigned_arm(
    tmp_path: Any,
) -> None:
    db = await _db(tmp_path)

    try:
        service, repository, metrics, _versioner = await _harness(db)
        candidate_id = await _candidate_id(repository)
        experiment = await service.start_candidate(candidate_id)

        store = TrajectoryStore(db)
        _, trajectory_id = await store.begin(goal="deploy", thread_id="thread-c")

        assignments = await service.prepare_task(
            task_text="please deploy the app to staging", unit_id="thread-c"
        )
        await service.bind_trajectory(trajectory_id, assignments)

        await store.append(
            trajectory_id,
            event_type="tool.call.delta",
            data={"id": "call-1", "name": "skill_view", "args": '{"name": "demo"}'},
            source="main",
        )
        await store.append(
            trajectory_id,
            event_type="tool.result",
            data={
                "name": "skill_view",
                "tool_call_id": "call-1",
                "content": "skill instructions",
                "status": None,
            },
            source="main",
        )
        await store.finish(trajectory_id, outcome="success", result="done")

        attributor = SkillExecutionAttributor(
            trajectories=store, repository=repository, metrics=metrics
        )
        recorded = await attributor.attribute(trajectory_id)

        assert recorded == ["demo"]

        rows = await db.fetch(
            "SELECT * FROM skill_execution_metrics WHERE trajectory_id = ?",
            (trajectory_id,),
        )
        assert len(rows) == 1
        assert rows[0]["experiment_id"] == experiment["id"]
        assert rows[0]["arm_kind"] == assignments[0].arm_kind
        assert rows[0]["unit_id"] == "thread-c"

        # Those rows now count towards the arm the experiment compares.
        arm_version = assignments[0].version
        summary = await SkillAnalyticsService(db).version_summary(
            "demo", arm_version, experiment_id=str(experiment["id"])
        )
        assert summary["total"] == 1

    finally:
        await db.close()


def test_settings_expose_experiment_and_regression_sections() -> None:
    settings = Settings.load()

    assert settings.skills.experiments.enabled is True
    assert settings.skills.experiments.default_strategy in {"ab", "thompson"}
    assert settings.skills.regression.enabled is True
    assert settings.skills.learning.auto_promote_initial is True
    assert settings.skills.learning.auto_experiment_upgrades is True


def test_skill_status_includes_experimenting() -> None:
    assert SkillStatus.EXPERIMENTING.value == "experimenting"
    assert SkillStatus.VERIFIED.value == "verified"
