"""Skill lifecycle: upgrade flow, rollback, version compare, regression detection."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.src.api.routes.skills import router
from server.src.skills.evaluation.regression import SkillRegressionDetector
from server.src.skills.promotion.promoter import SkillPromoter
from server.src.skills.service import SkillsService


# --------------------------------------------------------------------------
# upgrade_skill
# --------------------------------------------------------------------------


class FakeEvaluator:
    # One held-out case that did not grade, as the real report carries.
    case_results = [
        {
            "case_id": "heldout-001",
            "candidate": {
                "success": False,
                "skipped": False,
                "judge_reason": "missing final step",
            },
        },
        {
            "case_id": "heldout-002",
            "candidate": {"success": True, "skipped": False},
        },
    ]

    def __init__(self, verdict: str = "pass") -> None:
        self.verdict = verdict
        self.calls: list[str] = []

    async def evaluate(
        self,
        candidate_id: str,
        *,
        model_name: str | None = None,
    ) -> Any:
        self.calls.append(candidate_id)

        reasons = (
            []
            if self.verdict == "pass"
            else ["candidate success rate is below minimum"]
        )

        report = {"id": f"eval-{candidate_id}", "verdict": self.verdict}

        return SimpleNamespace(
            id=f"eval-{candidate_id}",
            verdict=self.verdict,
            comparison=SimpleNamespace(
                reasons=reasons,
                improvement_pass=self.verdict == "pass",
            ),
            case_results=self.case_results,
            model_dump=lambda mode="json": report,
        )


class FakePromoter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []

    async def promote(
        self,
        *,
        candidate_id: str,
        evaluation_id: str | None = None,
    ) -> Any:
        self.calls.append((candidate_id, evaluation_id))
        return SimpleNamespace(skill_name="skill-a", version="2.0.0")


async def test_upgrade_skill_promotes_on_pass() -> None:
    service = SimpleNamespace(
        evaluator=FakeEvaluator("pass"),
        promoter=FakePromoter(),
    )

    result = await SkillsService.upgrade_skill(
        service,
        candidate_id="cand-1",
        reason="better coverage",
    )

    assert result == {
        "status": "promoted",
        "skill": "skill-a",
        "version": "2.0.0",
        "reason": "better coverage",
    }
    assert service.evaluator.calls == ["cand-1"]
    assert service.promoter.calls == [("cand-1", "eval-cand-1")]


async def test_upgrade_skill_never_promotes_a_failed_evaluation() -> None:
    service = SimpleNamespace(
        evaluator=FakeEvaluator("fail"),
        promoter=FakePromoter(),
    )

    result = await SkillsService.upgrade_skill(service, candidate_id="cand-2")

    assert result == {
        "status": "rejected",
        "reason": "evaluation_failed",
        "evaluation": "eval-cand-2",
        "verdict": "fail",
        # Gate reasons first, then the held-out cases that failed. The
        # passing case is not reported.
        "reasons": [
            "candidate success rate is below minimum",
            "heldout-001: missing final step",
        ],
        "report": {"id": "eval-cand-2", "verdict": "fail"},
    }
    # The current active version must be left untouched.
    assert service.promoter.calls == []


async def test_upgrade_skill_reports_needs_review_separately() -> None:
    service = SimpleNamespace(
        evaluator=FakeEvaluator("needs_review"),
        promoter=FakePromoter(),
    )

    result = await SkillsService.upgrade_skill(service, candidate_id="cand-3")

    assert result["status"] == "rejected"
    assert result["reason"] == "evaluation_needs_review"
    assert result["verdict"] == "needs_review"
    assert service.promoter.calls == []


# --------------------------------------------------------------------------
# rollback_to_version
# --------------------------------------------------------------------------


class FakeVersionStore:
    def __init__(self, *, active: dict | None, versions: dict[str, dict]) -> None:
        self._active = active
        self._versions = versions

    async def get_active(self, skill_name: str) -> dict | None:
        return self._active

    async def get_version(self, skill_name: str, version: str) -> dict | None:
        return self._versions.get(version)


class FakeVersioner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str | None]] = []

    async def rollback(
        self,
        skill_name: str,
        version: str,
        *,
        reason: str | None = None,
    ) -> Any:
        self.calls.append((skill_name, version, reason))
        return SimpleNamespace(
            version=version,
            previous_version="3.0.0",
        )


def _promoter(
    active: dict | None,
    versions: dict[str, dict],
) -> Any:
    """A SkillPromoter with fakes swapped in for its repository and versioner."""

    return SimpleNamespace(
        _repository=FakeVersionStore(active=active, versions=versions),
        _versioner=FakeVersioner(),
    )


async def test_rollback_restores_the_requested_version() -> None:
    host = _promoter(
        active={"version": "3.0.0"},
        versions={"2.0.0": {"version": "2.0.0"}},
    )

    result = await SkillPromoter.rollback_to_version(
        host,
        skill_name="skill-a",
        version="2.0.0",
        reason="metrics dropped",
    )

    assert result == {
        "skill": "skill-a",
        "rolled_back_from": "3.0.0",
        "rolled_back_to": "2.0.0",
        "reason": "metrics dropped",
    }
    assert host._versioner.calls == [("skill-a", "2.0.0", "metrics dropped")]


async def test_rollback_rejects_unknown_or_already_active_versions() -> None:
    host = _promoter(
        active={"version": "3.0.0"},
        versions={"3.0.0": {"version": "3.0.0"}},
    )

    with pytest.raises(ValueError, match="not found"):
        await SkillPromoter.rollback_to_version(
            host, skill_name="skill-a", version="9.9.9"
        )

    with pytest.raises(ValueError, match="already active"):
        await SkillPromoter.rollback_to_version(
            host, skill_name="skill-a", version="3.0.0"
        )

    assert host._versioner.calls == []


async def test_rollback_requires_an_active_version() -> None:
    host = _promoter(active=None, versions={"1.0.0": {"version": "1.0.0"}})

    with pytest.raises(ValueError, match="no active version"):
        await SkillPromoter.rollback_to_version(
            host, skill_name="skill-a", version="1.0.0"
        )


# --------------------------------------------------------------------------
# regression detector
# --------------------------------------------------------------------------


def test_regression_detector_flags_drops() -> None:
    detector = SkillRegressionDetector()

    result = detector.detect(
        {"success_rate": 0.9, "tool_errors": 1},
        {"success_rate": 0.6, "tool_errors": 4},
    )

    assert result["regression"] is True
    assert set(result["issues"]) == {"success_rate_drop", "tool_failure_increase"}


def test_regression_detector_accepts_improvements_and_dicts() -> None:
    detector = SkillRegressionDetector()

    result = detector.detect(
        {"success_rate": 0.5, "tool_failures": 5},
        {"success_rate": 0.8, "tool_failures": 2},
    )

    assert result == {"regression": False, "issues": []}


def test_regression_detector_tolerates_missing_fields() -> None:
    detector = SkillRegressionDetector()

    assert detector.detect({}, {}) == {"regression": False, "issues": []}


# --------------------------------------------------------------------------
# routes
# --------------------------------------------------------------------------


class RoutePromoter:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def rollback_to_version(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if kwargs["version"] == "missing":
            raise ValueError("Version missing not found")
        return {
            "skill": kwargs["skill_name"],
            "rolled_back_from": "3.0.0",
            "rolled_back_to": kwargs["version"],
            "reason": kwargs.get("reason"),
        }


class RouteVersioner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    async def compare_versions(
        self, skill_name: str, old: str, new: str
    ) -> dict[str, Any]:
        self.calls.append((skill_name, old, new))
        if old == "nope":
            raise ValueError("Unknown version nope")
        return {
            "skill": skill_name,
            "from": {"version": old, "metadata": {}},
            "to": {"version": new, "metadata": {}},
            "changed": True,
        }


class RouteService:
    def __init__(self) -> None:
        self.promoter = RoutePromoter()
        self.versioner = RouteVersioner()
        self.upgrade_calls: list[tuple[str, str | None]] = []
        self.upgrade_result: dict[str, Any] = {
            "status": "promoted",
            "skill": "skill-a",
            "version": "2.0.0",
            "reason": None,
        }

    async def upgrade_skill(
        self, *, candidate_id: str, reason: str | None = None
    ) -> dict[str, Any]:
        self.upgrade_calls.append((candidate_id, reason))
        if candidate_id == "missing":
            raise ValueError("skill candidate 'missing' not found")
        return self.upgrade_result


@pytest.fixture()
def lifecycle_client() -> tuple[TestClient, RouteService]:
    app = FastAPI()
    app.include_router(router)

    service = RouteService()
    app.state.skills_service = service

    return TestClient(app), service


def test_upgrade_route_promotes(lifecycle_client: tuple[TestClient, RouteService]) -> None:
    client, service = lifecycle_client

    response = client.post(
        "/skills/upgrade",
        json={"candidate_id": "cand-9", "reason": "reviewed"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "promoted"
    assert service.upgrade_calls == [("cand-9", "reviewed")]


def test_upgrade_route_maps_value_error_to_409(
    lifecycle_client: tuple[TestClient, RouteService],
) -> None:
    client, _ = lifecycle_client

    response = client.post("/skills/upgrade", json={"candidate_id": "missing"})

    assert response.status_code == 409
    assert "not found" in response.json()["detail"]


def test_rollback_route_requires_a_version(
    lifecycle_client: tuple[TestClient, RouteService],
) -> None:
    client, service = lifecycle_client

    response = client.post("/skills/skill-a/rollback", json={"reason": "bad"})

    assert response.status_code == 400
    assert service.promoter.calls == []


def test_rollback_route_restores_a_version(
    lifecycle_client: tuple[TestClient, RouteService],
) -> None:
    client, service = lifecycle_client

    response = client.post(
        "/skills/skill-a/rollback",
        json={"version": "2.0.0", "reason": "regression"},
    )

    assert response.status_code == 200
    assert response.json()["rolled_back_to"] == "2.0.0"
    assert service.promoter.calls == [
        {
            "skill_name": "skill-a",
            "version": "2.0.0",
            "reason": "regression",
        }
    ]


def test_rollback_route_maps_missing_version_to_409(
    lifecycle_client: tuple[TestClient, RouteService],
) -> None:
    client, _ = lifecycle_client

    response = client.post("/skills/skill-a/rollback", json={"version": "missing"})

    assert response.status_code == 409


def test_compare_route_diffs_two_versions(
    lifecycle_client: tuple[TestClient, RouteService],
) -> None:
    client, service = lifecycle_client

    response = client.post(
        "/skills/skill-a/versions/compare",
        json={"from_version": "1.0.0", "to_version": "2.0.0"},
    )

    assert response.status_code == 200
    assert response.json()["changed"] is True
    assert service.versioner.calls == [("skill-a", "1.0.0", "2.0.0")]


def test_compare_route_maps_unknown_version_to_409(
    lifecycle_client: tuple[TestClient, RouteService],
) -> None:
    client, _ = lifecycle_client

    response = client.post(
        "/skills/skill-a/versions/compare",
        json={"from_version": "nope", "to_version": "2.0.0"},
    )

    assert response.status_code == 409


async def test_upgrade_with_stored_evaluation_does_not_replay() -> None:
    class SavedEvaluationRepository:
        async def get_evaluation(self, evaluation_id: str):
            return {"id": evaluation_id, "candidate_id": "cand-4", "verdict": "pass"}

    service = SimpleNamespace(
        evaluator=FakeEvaluator("pass"),
        promoter=FakePromoter(),
        repository=SavedEvaluationRepository(),
    )
    result = await SkillsService.upgrade_skill(
        service, candidate_id="cand-4", evaluation_id="eval-previous",
    )
    assert result["status"] == "promoted"
    assert service.evaluator.calls == []
    assert service.promoter.calls == [("cand-4", "eval-previous")]
