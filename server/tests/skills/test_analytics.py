"""Analytics, version regression policy, A/B experiments and their routes."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import patch

from server.src.api.routes.skills import router
from server.src.memory.storage.sqlite import SQLiteDatabase
from server.src.skills.analytics import SkillAnalytics, SkillMetricsCollector
from server.src.skills.experiments import SkillExperimentRouter
from server.src.skills.regression import AutomaticRollback, VersionRegressionDetector
from server.src.skills.service import SkillsService


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


async def _seed(
    db: SQLiteDatabase,
    *,
    skill: str,
    version: str,
    count: int,
    success: bool = True,
    latency: float = 100.0,
    tokens: int = 10,
    failures: int = 0,
) -> None:
    """Record ``count`` identical execution samples for one version."""

    collector = SkillMetricsCollector(db)

    for _ in range(count):
        await collector.record(
            skill_name=skill,
            skill_version=version,
            trajectory_id=None,
            success=success,
            latency_ms=latency,
            tokens=tokens,
            tool_failures=failures,
        )


async def _db(tmp_path: Any) -> SQLiteDatabase:
    db = SQLiteDatabase(tmp_path / "analytics.db")
    await db.open()
    return db


# --------------------------------------------------------------------------
# SkillMetricsCollector / SkillAnalytics
# --------------------------------------------------------------------------


async def test_record_stores_a_sample_and_returns_its_id(tmp_path: Any) -> None:
    db = await _db(tmp_path)

    try:
        metric_id = await SkillMetricsCollector(db).record(
            skill_name="demo",
            skill_version="1.0.0",
            trajectory_id="traj-1",
            success=True,
            latency_ms=120.5,
            tokens=64,
            tool_failures=0,
        )

        row = await db.fetchone(
            "SELECT * FROM skill_execution_metrics WHERE id = ?",
            (metric_id,),
        )

        assert row is not None
        assert row["skill_name"] == "demo"
        assert row["skill_version"] == "1.0.0"
        assert row["success"] == 1
        assert float(row["latency_ms"]) == 120.5
        assert int(row["tokens_used"]) == 64
    finally:
        await db.close()


async def test_summary_aggregates_every_sample(tmp_path: Any) -> None:
    db = await _db(tmp_path)

    try:
        await _seed(db, skill="demo", version="1.0.0", count=3)
        await _seed(
            db, skill="demo", version="2.0.0", count=1, success=False, failures=4
        )

        summary = await SkillAnalytics(db).summary("demo")

        assert summary["skill"] == "demo"
        assert summary["total"] == 4
        # 3 successes out of 4 executions.
        assert float(summary["success_rate"]) == pytest.approx(0.75)
        assert summary["latency"] == 100.0
        assert summary["failures"] == 1.0  # 4 failures over 4 rows
        assert summary["versions"] == [
            {"version": "1.0.0", "executions": 3},
            {"version": "2.0.0", "executions": 1},
        ]
    finally:
        await db.close()


async def test_summary_scopes_to_one_version(tmp_path: Any) -> None:
    db = await _db(tmp_path)

    try:
        await _seed(db, skill="demo", version="1.0.0", count=2)
        await _seed(db, skill="demo", version="2.0.0", count=5)

        scoped = await SkillAnalytics(db).summary("demo", "1.0.0")

        assert scoped["total"] == 2
        # Version coverage still reports every version of the skill.
        assert [item["executions"] for item in scoped["versions"]] == [2, 5]
    finally:
        await db.close()


async def test_summary_normalizes_an_empty_result_to_numbers(tmp_path: Any) -> None:
    """AVG() over no rows is NULL — callers do arithmetic, so it must be 0."""

    db = await _db(tmp_path)

    try:
        summary = await SkillAnalytics(db).summary("never-executed")

        assert summary["total"] == 0
        assert summary["success_rate"] == 0
        assert summary["latency"] == 0
        assert summary["tokens"] == 0
        assert summary["failures"] == 0
        assert summary["versions"] == []
        # None here would raise TypeError in every threshold comparison.
        for key in ("total", "success_rate", "latency", "tokens", "failures"):
            assert summary[key] is not None
    finally:
        await db.close()


# --------------------------------------------------------------------------
# VersionRegressionDetector
# --------------------------------------------------------------------------


async def test_detector_refuses_to_decide_without_data_on_both_sides(
    tmp_path: Any,
) -> None:
    db = await _db(tmp_path)

    try:
        await _seed(db, skill="demo", version="1.0.0", count=5, success=False)
        detector = VersionRegressionDetector(SkillAnalytics(db))

        missing_current = await detector.check("demo", "1.0.0", "2.0.0")
        missing_stable = await detector.check("demo", "9.9.9", "1.0.0")

        # An empty side would otherwise average to a perfect 0.0 failure rate.
        for result in (missing_current, missing_stable):
            assert result["regression"] is False
            assert result["insufficient_data"] is True
            assert result["reasons"] == []
    finally:
        await db.close()


async def test_detector_flags_a_success_rate_drop(tmp_path: Any) -> None:
    db = await _db(tmp_path)

    try:
        await _seed(db, skill="demo", version="1.0.0", count=10, success=True)
        await _seed(db, skill="demo", version="2.0.0", count=10, success=False)

        result = await VersionRegressionDetector(SkillAnalytics(db)).check(
            "demo", "1.0.0", "2.0.0"
        )

        assert result["regression"] is True
        assert result["reasons"] == ["success_rate_drop"]
        assert result["severity"] == "medium"
        assert result["insufficient_data"] is False
    finally:
        await db.close()


async def test_detector_flags_latency_and_tool_failure_regressions(
    tmp_path: Any,
) -> None:
    db = await _db(tmp_path)

    try:
        await _seed(db, skill="demo", version="1.0.0", count=5, latency=100.0)
        await _seed(
            db,
            skill="demo",
            version="2.0.0",
            count=5,
            latency=400.0,
            failures=3,
        )

        result = await VersionRegressionDetector(SkillAnalytics(db)).check(
            "demo", "1.0.0", "2.0.0"
        )

        assert result["regression"] is True
        assert set(result["reasons"]) == {"latency_regression", "tool_failures"}
        # Two or more concurrent reasons escalate the severity.
        assert result["severity"] == "high"
    finally:
        await db.close()


async def test_detector_accepts_versions_within_thresholds(tmp_path: Any) -> None:
    db = await _db(tmp_path)

    try:
        await _seed(db, skill="demo", version="1.0.0", count=10)
        # A 0.10 success-rate drop sits under the 0.15 tolerance.
        await _seed(db, skill="demo", version="2.0.0", count=9, success=True)
        await _seed(db, skill="demo", version="2.0.0", count=1, success=False)

        result = await VersionRegressionDetector(SkillAnalytics(db)).check(
            "demo", "1.0.0", "2.0.0"
        )

        assert result == {
            "regression": False,
            "reasons": [],
            "insufficient_data": False,
        }
    finally:
        await db.close()


# --------------------------------------------------------------------------
# AutomaticRollback
# --------------------------------------------------------------------------


class FakeRollbackPromoter:
    def __init__(self, *, error: str | None = None) -> None:
        self.calls: list[tuple[str, str, str | None]] = []
        self._error = error

    async def rollback_to_version(
        self,
        *,
        skill_name: str,
        version: str,
        reason: str | None = None,
    ) -> dict[str, Any]:
        self.calls.append((skill_name, version, reason))

        if self._error is not None:
            raise ValueError(self._error)

        return {
            "skill": skill_name,
            "rolled_back_from": "2.0.0",
            "rolled_back_to": version,
            "reason": reason,
        }


def _policy(db: SQLiteDatabase, promoter: Any) -> AutomaticRollback:
    return AutomaticRollback(
        detector=VersionRegressionDetector(SkillAnalytics(db)),
        promoter=promoter,
        db=db,
    )


async def test_policy_is_a_no_op_when_nothing_regressed(tmp_path: Any) -> None:
    db = await _db(tmp_path)

    try:
        await _seed(db, skill="demo", version="1.0.0", count=5)
        await _seed(db, skill="demo", version="2.0.0", count=5)

        promoter = FakeRollbackPromoter()
        result = await _policy(db, promoter).evaluate("demo", "1.0.0", "2.0.0")

        assert result["regression"] is False
        assert promoter.calls == []
        assert await _policy(db, promoter).history("demo") == []
    finally:
        await db.close()


async def test_policy_rolls_back_and_marks_the_log(tmp_path: Any) -> None:
    db = await _db(tmp_path)

    try:
        await _seed(db, skill="demo", version="1.0.0", count=10, success=True)
        await _seed(db, skill="demo", version="2.0.0", count=10, success=False)

        promoter = FakeRollbackPromoter()
        result = await _policy(db, promoter).evaluate("demo", "1.0.0", "2.0.0")

        assert result["rolled_back"] is True
        assert result["rollback"] == {
            "skill": "demo",
            "from": "2.0.0",
            "to": "1.0.0",
        }
        assert promoter.calls == [("demo", "1.0.0", "success_rate_drop")]

        history = await _policy(db, promoter).history("demo")
        assert len(history) == 1
        assert history[0]["rolled_back"] is True
        assert history[0]["bad_version"] == "2.0.0"
        assert history[0]["stable_version"] == "1.0.0"
    finally:
        await db.close()


async def test_policy_keeps_the_log_when_the_rollback_fails(tmp_path: Any) -> None:
    db = await _db(tmp_path)

    try:
        await _seed(db, skill="demo", version="1.0.0", count=10, success=True)
        await _seed(db, skill="demo", version="2.0.0", count=10, success=False)

        promoter = FakeRollbackPromoter(error="version 1.0.0 already active")
        result = await _policy(db, promoter).evaluate("demo", "1.0.0", "2.0.0")

        assert result["rolled_back"] is False
        assert result["error"] == "version 1.0.0 already active"
        assert result["regression_id"]

        # The regression is still durable, just not restored.
        history = await _policy(db, promoter).history("demo")
        assert len(history) == 1
        assert history[0]["rolled_back"] is False
    finally:
        await db.close()


async def test_policy_log_is_newest_first(tmp_path: Any) -> None:
    db = await _db(tmp_path)

    try:
        await _seed(db, skill="demo", version="1.0.0", count=10, success=True)
        await _seed(db, skill="demo", version="2.0.0", count=10, success=False)
        await _seed(db, skill="demo", version="3.0.0", count=10, success=False)

        promoter = FakeRollbackPromoter()
        policy = _policy(db, promoter)
        await policy.evaluate("demo", "1.0.0", "2.0.0")
        await policy.evaluate("demo", "1.0.0", "3.0.0")

        # Pin the timestamps so the ordering assertion cannot depend on how
        # quickly the two calls above landed.
        await db.execute(
            "UPDATE skill_regressions SET created_at = ? WHERE bad_version = ?",
            ("2026-01-01T00:00:00+00:00", "2.0.0"),
        )

        history = await policy.history("demo")

        assert [row["bad_version"] for row in history] == ["3.0.0", "2.0.0"]
    finally:
        await db.close()


# --------------------------------------------------------------------------
# SkillExperimentRouter
# --------------------------------------------------------------------------


async def test_choose_version_without_an_experiment_returns_none(
    tmp_path: Any,
) -> None:
    db = await _db(tmp_path)

    try:
        router_ = SkillExperimentRouter(db)
        assert await router_.choose_version("demo") is None
        assert await router_.list("demo") == []
    finally:
        await db.close()


async def test_create_rejects_bad_traffic_and_matching_versions(
    tmp_path: Any,
) -> None:
    db = await _db(tmp_path)

    try:
        router_ = SkillExperimentRouter(db)

        with pytest.raises(ValueError, match="between 1 and 100"):
            await router_.create(
                skill_name="demo",
                control_version="1.0.0",
                experiment_version="2.0.0",
                traffic_percent=0,
            )

        with pytest.raises(ValueError, match="must differ"):
            await router_.create(
                skill_name="demo",
                control_version="1.0.0",
                experiment_version="1.0.0",
                traffic_percent=50,
            )

        assert await router_.list("demo") == []
    finally:
        await db.close()


async def test_create_supersedes_a_running_experiment(tmp_path: Any) -> None:
    db = await _db(tmp_path)

    try:
        router_ = SkillExperimentRouter(db)
        first = await router_.create(
            skill_name="demo",
            control_version="1.0.0",
            experiment_version="2.0.0",
            traffic_percent=50,
        )
        second = await router_.create(
            skill_name="demo",
            control_version="1.0.0",
            experiment_version="3.0.0",
            traffic_percent=10,
        )

        listed = await router_.list("demo")

        # choose_version reads the newest running row, so only one may exist.
        running = [row for row in listed if row["status"] == "running"]
        superseded = [row for row in listed if row["status"] == "superseded"]

        assert [row["id"] for row in running] == [second["id"]]
        assert [row["id"] for row in superseded] == [first["id"]]
        # create() reports the status at creation time, not the final one.
        assert first["status"] == "running"
    finally:
        await db.close()


async def test_choose_version_honours_the_traffic_split(tmp_path: Any) -> None:
    db = await _db(tmp_path)

    try:
        router_ = SkillExperimentRouter(db)
        await router_.create(
            skill_name="demo",
            control_version="1.0.0",
            experiment_version="2.0.0",
            traffic_percent=49,
        )

        with patch(
            "server.src.skills.experiments.router.random.randint",
            return_value=49,
        ):
            assert await router_.choose_version("demo") == "2.0.0"

        with patch(
            "server.src.skills.experiments.router.random.randint",
            return_value=50,
        ):
            assert await router_.choose_version("demo") == "1.0.0"
    finally:
        await db.close()


async def test_stop_only_stops_running_experiments(tmp_path: Any) -> None:
    db = await _db(tmp_path)

    try:
        router_ = SkillExperimentRouter(db)
        await router_.create(
            skill_name="demo",
            control_version="1.0.0",
            experiment_version="2.0.0",
            traffic_percent=50,
        )
        await router_.create(
            skill_name="demo",
            control_version="1.0.0",
            experiment_version="3.0.0",
            traffic_percent=50,
        )

        stopped = await router_.stop("demo")

        assert stopped == 1  # the older one was already superseded
        assert await router_.choose_version("demo") is None
        assert await router_.stop("demo") == 0
    finally:
        await db.close()


# --------------------------------------------------------------------------
# SkillsService methods
# --------------------------------------------------------------------------


class FakeVersionRepository:
    def __init__(self, versions: set[str]) -> None:
        self._versions = versions

    async def get_version(self, skill_name: str, version: str) -> Any:
        if version in self._versions:
            return {"skill_name": skill_name, "version": version}
        return None


class RecordingExperiments:
    def __init__(self) -> None:
        self.create_calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> dict[str, Any]:
        self.create_calls.append(kwargs)
        return {"id": "exp-1", "status": "running", **kwargs}


async def test_create_experiment_rejects_unknown_versions() -> None:
    experiments = RecordingExperiments()
    host = SimpleNamespace(
        repository=FakeVersionRepository({"1.0.0"}),
        experiments=experiments,
    )

    with pytest.raises(ValueError, match="not found"):
        await SkillsService.create_experiment(
            host,
            skill_name="demo",
            control_version="1.0.0",
            experiment_version="9.9.9",
            traffic_percent=10,
        )

    assert experiments.create_calls == []


async def test_create_experiment_delegates_when_both_versions_exist() -> None:
    experiments = RecordingExperiments()
    host = SimpleNamespace(
        repository=FakeVersionRepository({"1.0.0", "2.0.0"}),
        experiments=experiments,
    )

    result = await SkillsService.create_experiment(
        host,
        skill_name="demo",
        control_version="1.0.0",
        experiment_version="2.0.0",
        traffic_percent=25,
    )

    assert result["status"] == "running"
    assert experiments.create_calls == [
        {
            "skill_name": "demo",
            "control_version": "1.0.0",
            "experiment_version": "2.0.0",
            "traffic_percent": 25,
        }
    ]


async def test_skill_analytics_enriches_the_summary() -> None:
    class FakeAnalytics:
        async def summary(self, skill_name: str, version: str | None = None) -> Any:
            return {"skill": skill_name, "total": 3}

    class FakePolicy:
        async def history(self, skill_name: str, limit: int = 50) -> Any:
            return [{"bad_version": "2.0.0"}]

    class FakeList:
        async def list(self, skill_name: str) -> Any:
            return [{"status": "running"}]

    host = SimpleNamespace(
        analytics=FakeAnalytics(),
        rollback_policy=FakePolicy(),
        experiments=FakeList(),
    )

    summary = await SkillsService.skill_analytics(host, "demo", version="1.0.0")

    assert summary["skill"] == "demo"
    assert summary["regressions"] == [{"bad_version": "2.0.0"}]
    assert summary["experiments"] == [{"status": "running"}]


# --------------------------------------------------------------------------
# routes
# --------------------------------------------------------------------------


class AnalyticsRouteService:
    def __init__(self) -> None:
        self.analytics_calls: list[tuple[str, str | None]] = []
        self.experiment_calls: list[dict[str, Any]] = []
        self.check_calls: list[tuple[str, str, str]] = []
        self.fail_experiment = False
        self.fail_check = False

    async def skill_analytics(
        self, skill_name: str, *, version: str | None = None
    ) -> dict[str, Any]:
        self.analytics_calls.append((skill_name, version))
        return {"skill": skill_name, "total": 0, "regressions": [], "experiments": []}

    async def create_experiment(
        self,
        *,
        skill_name: str,
        control_version: str,
        experiment_version: str,
        traffic_percent: int,
    ) -> dict[str, Any]:
        self.experiment_calls.append(
            {
                "skill_name": skill_name,
                "control_version": control_version,
                "experiment_version": experiment_version,
                "traffic_percent": traffic_percent,
            }
        )

        if self.fail_experiment:
            raise ValueError(f"skill version {skill_name}@9.9.9 not found")

        return {
            "id": "exp-1",
            "skill_name": skill_name,
            "control_version": control_version,
            "experiment_version": experiment_version,
            "traffic_percent": traffic_percent,
            "status": "running",
        }

    async def enforce_rollback(
        self,
        skill_name: str,
        *,
        stable_version: str,
        current_version: str,
    ) -> dict[str, Any]:
        self.check_calls.append((skill_name, stable_version, current_version))

        if self.fail_check:
            raise ValueError("skill not found")

        return {"regression": False, "reasons": [], "insufficient_data": True}


@pytest.fixture()
def analytics_client() -> tuple[TestClient, AnalyticsRouteService]:
    app = FastAPI()
    app.include_router(router)

    service = AnalyticsRouteService()
    app.state.skills_service = service

    return TestClient(app), service


def test_experiment_route_opens_a_split(
    analytics_client: tuple[TestClient, AnalyticsRouteService],
) -> None:
    client, service = analytics_client

    response = client.post(
        "/skills/experiments",
        json={
            "skill_name": "demo",
            "control_version": "1.0.0",
            "experiment_version": "2.0.0",
            "traffic_percent": 30,
        },
    )

    assert response.status_code == 201
    assert response.json()["status"] == "running"
    assert service.experiment_calls == [
        {
            "skill_name": "demo",
            "control_version": "1.0.0",
            "experiment_version": "2.0.0",
            "traffic_percent": 30,
        }
    ]


def test_experiment_route_maps_value_error_to_409(
    analytics_client: tuple[TestClient, AnalyticsRouteService],
) -> None:
    client, service = analytics_client
    service.fail_experiment = True

    response = client.post(
        "/skills/experiments",
        json={
            "skill_name": "demo",
            "control_version": "1.0.0",
            "experiment_version": "9.9.9",
            "traffic_percent": 30,
        },
    )

    assert response.status_code == 409
    assert "not found" in response.json()["detail"]


def test_experiment_route_rejects_out_of_range_traffic(
    analytics_client: tuple[TestClient, AnalyticsRouteService],
) -> None:
    client, service = analytics_client

    response = client.post(
        "/skills/experiments",
        json={
            "skill_name": "demo",
            "control_version": "1.0.0",
            "experiment_version": "2.0.0",
            "traffic_percent": 0,
        },
    )

    assert response.status_code == 422
    assert service.experiment_calls == []


def test_analytics_route_returns_the_summary(
    analytics_client: tuple[TestClient, AnalyticsRouteService],
) -> None:
    client, service = analytics_client

    response = client.get("/skills/demo/analytics")

    assert response.status_code == 200
    assert response.json() == {
        "skill": "demo",
        "total": 0,
        "regressions": [],
        "experiments": [],
    }
    assert service.analytics_calls == [("demo", None)]


def test_analytics_route_forwards_the_version_filter(
    analytics_client: tuple[TestClient, AnalyticsRouteService],
) -> None:
    client, service = analytics_client

    response = client.get("/skills/demo/analytics", params={"version": "2.0.0"})

    assert response.status_code == 200
    assert service.analytics_calls == [("demo", "2.0.0")]


def test_regression_check_route_runs_the_policy(
    analytics_client: tuple[TestClient, AnalyticsRouteService],
) -> None:
    client, service = analytics_client

    response = client.post(
        "/skills/demo/regressions/check",
        json={"stable_version": "1.0.0", "current_version": "2.0.0"},
    )

    assert response.status_code == 200
    assert response.json()["insufficient_data"] is True
    assert service.check_calls == [("demo", "1.0.0", "2.0.0")]


def test_regression_check_route_maps_value_error_to_409(
    analytics_client: tuple[TestClient, AnalyticsRouteService],
) -> None:
    client, service = analytics_client
    service.fail_check = True

    response = client.post(
        "/skills/demo/regressions/check",
        json={"stable_version": "1.0.0", "current_version": "2.0.0"},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "skill not found"
