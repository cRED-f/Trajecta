from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from server.src.config import Settings
from server.src.memory.storage.sqlite import SQLiteDatabase
from server.src.skills.learning.coordinator import SkillLearningCoordinator
from server.src.skills.trajectory_store import TrajectoryStore


class FakeMiner:
    def __init__(
        self,
        *,
        result: list[dict[str, Any]] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.result = result or []
        self.error = error
        self.calls = 0

    async def mine(self, **_: Any) -> list[dict[str, Any]]:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return list(self.result)


class FakeEvaluator:
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
        return SimpleNamespace(
            id=f"eval-{candidate_id}",
            verdict=self.verdict,
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
        return SimpleNamespace(skill_name="demo", version="0.1.0")


async def _successful_trajectory(store: TrajectoryStore, n: int) -> str:
    _, trajectory_id = await store.begin(
        goal=f"repeat task {n}",
        thread_id=f"thread-{n}",
    )
    await store.finish(trajectory_id, outcome="success", result="ok")
    return trajectory_id


@pytest.mark.asyncio
async def test_learning_runs_after_threshold(tmp_path) -> None:
    db = SQLiteDatabase(tmp_path / "learning.db")
    await db.open()

    try:
        trajectories = TrajectoryStore(db)

        settings = Settings.model_validate(
            {
                "skills": {
                    "learning": {
                        "enabled": True,
                        "trigger_every_successes": 4,
                        "minimum_occurrences": 4,
                        "auto_evaluate": True,
                        "auto_promote": True,
                    }
                }
            }
        )

        miner = FakeMiner(result=[{"id": "candidate-1"}])
        evaluator = FakeEvaluator("pass")
        promoter = FakePromoter()

        coordinator = SkillLearningCoordinator(
            settings=settings,
            db=db,
            trajectories=trajectories,
            miner=miner,  # type: ignore[arg-type]
            evaluator=evaluator,  # type: ignore[arg-type]
            promoter=promoter,  # type: ignore[arg-type]
        )

        # Only three successes; threshold is four.
        for index in range(3):
            await _successful_trajectory(trajectories, index)

        skipped = await coordinator.run_once()
        assert skipped["status"] == "skipped"
        assert miner.calls == 0

        # Fourth successful trajectory.
        await _successful_trajectory(trajectories, 4)

        result = await coordinator.run_once()
        assert result["status"] == "complete"
        assert result["created_count"] == 1
        assert result["evaluated_count"] == 1
        assert result["verified_count"] == 1
        assert result["promoted_count"] == 1
        assert miner.calls == 1
        assert evaluator.calls == ["candidate-1"]
        assert promoter.calls == [("candidate-1", "eval-candidate-1")]

        status = await coordinator.status()
        assert status["success_count_checkpoint"] == 4
        assert status["pending_successes"] == 0

    finally:
        await db.close()


@pytest.mark.asyncio
async def test_failed_mining_does_not_advance_checkpoint(tmp_path) -> None:
    db = SQLiteDatabase(tmp_path / "learning.db")
    await db.open()

    try:
        trajectories = TrajectoryStore(db)

        settings = Settings.model_validate(
            {
                "skills": {
                    "learning": {
                        "enabled": True,
                        "trigger_every_successes": 1,
                    }
                }
            }
        )

        miner = FakeMiner(error=RuntimeError("synthesis unavailable"))

        coordinator = SkillLearningCoordinator(
            settings=settings,
            db=db,
            trajectories=trajectories,
            miner=miner,  # type: ignore[arg-type]
            evaluator=FakeEvaluator(),  # type: ignore[arg-type]
            promoter=FakePromoter(),  # type: ignore[arg-type]
        )

        await _successful_trajectory(trajectories, 1)

        with pytest.raises(RuntimeError, match="synthesis unavailable"):
            await coordinator.run_once()

        status = await coordinator.status()

        # Critical behavior: a failed mining pass can be retried.
        assert status["success_count_checkpoint"] == 0
        assert status["pending_successes"] == 1
        assert status["state"]["last_status"] == "error"

    finally:
        await db.close()
