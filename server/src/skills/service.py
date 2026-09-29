"""Skills service — wires mining/evaluation/promotion persistence for the API.

Owns the skill repository, trajectory store, replay fixtures, skill miner,
evaluator, versioner, promoter and the automatic learning coordinator. REST
routes reach it through ``request.app.state.skills_service``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from server.src.config import Settings
from server.src.memory.procedural.store import ProceduralMemory
from server.src.skills.analytics import (
    SkillAnalytics,
    SkillExecutionAttributor,
    SkillMetricsCollector,
)
from server.src.skills.evaluation.evaluator import SkillEvaluator
from server.src.skills.evaluation.fixtures import ReplayFixtureStore
from server.src.skills.evaluation.replay import DeepAgentReplayExecutor, SkillReplay
from server.src.skills.evaluation.regression import SkillRegressionDetector
from server.src.skills.experiments import SkillExperimentRouter
from server.src.skills.learning import SkillLearningCoordinator
from server.src.skills.promotion.promoter import SkillPromoter
from server.src.skills.regression import AutomaticRollback, VersionRegressionDetector
from server.src.skills.repository import SkillRepository
from server.src.skills.skill_miner.miner import SkillMiner
from server.src.skills.trajectory_store import TrajectoryStore
from server.src.skills.versioning.versioner import SkillVersioner

if TYPE_CHECKING:
    from server.src.memory.provider import MemoryProvider
    from server.src.tools.personal import PersonalToolProvider


class SkillsService:
    def __init__(
        self,
        settings: Settings,
        memory: "MemoryProvider",
        personal_tools: "PersonalToolProvider",
    ) -> None:
        if memory.sqlite is None:
            raise RuntimeError("MemoryProvider must be opened before SkillsService")

        # -- Shared persistence ---------------------------------------
        repository = SkillRepository(memory.sqlite)
        trajectories = TrajectoryStore(memory.sqlite)
        replay_fixtures = ReplayFixtureStore(settings, memory.sqlite)

        # -- Replay evaluation ----------------------------------------
        executor = DeepAgentReplayExecutor(
            settings=settings,
            memory=memory,
            personal_tools=personal_tools,
            replay_fixtures=replay_fixtures,
        )
        replay = SkillReplay(executor)

        self.repository = repository
        self.trajectories = trajectories
        self.replay_fixtures = replay_fixtures
        self.evaluator = SkillEvaluator(repository=repository, replay=replay)

        # -- Versioning / promotion -----------------------------------
        self.versioner = SkillVersioner(
            repository=repository,
            procedural=ProceduralMemory(memory),
        )
        self.promoter = SkillPromoter(
            repository=repository,
            versioner=self.versioner,
        )

        # -- Mining ----------------------------------------------------
        self.miner = SkillMiner(
            settings=settings,
            trajectories=trajectories,
            repository=repository,
        )

        # -- Automatic learning loop -----------------------------------
        self.learning = SkillLearningCoordinator(
            settings=settings,
            db=memory.sqlite,
            trajectories=trajectories,
            miner=self.miner,
            evaluator=self.evaluator,
            promoter=self.promoter,
        )

        self.regression = SkillRegressionDetector()

        # -- Analytics / regression policy / A/B experiments -----------
        self.metrics = SkillMetricsCollector(memory.sqlite)
        self.analytics = SkillAnalytics(memory.sqlite)
        self.experiments = SkillExperimentRouter(memory.sqlite)
        self.version_regression = VersionRegressionDetector(
            analytics=self.analytics,
        )
        self.rollback_policy = AutomaticRollback(
            detector=self.version_regression,
            promoter=self.promoter,
            db=memory.sqlite,
        )
        # Reads the same trajectory store chat writes to, so live runs
        # become metrics rows without another copy of the data.
        self.execution = SkillExecutionAttributor(
            trajectories=trajectories,
            repository=repository,
            metrics=self.metrics,
        )

    async def reject_candidate(
        self,
        candidate_id: str,
        *,
        reason: str,
    ) -> dict[str, Any]:
        return await self.promoter.reject(candidate_id, reason=reason)

    async def upgrade_skill(
        self,
        *,
        candidate_id: str,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Evaluate a candidate and promote it only if it passes.

        An upgrade never swaps the active version directly: it runs the normal
        evaluate -> promote path, which creates the next version and
        activates it. A failing evaluation leaves the current version alone.
        """

        evaluation = await self.evaluator.evaluate(candidate_id)

        if evaluation.verdict != "pass":
            return {
                "status": "rejected",
                "reason": "evaluation_failed",
                "evaluation": evaluation.id,
            }

        promoted = await self.promoter.promote(
            candidate_id=candidate_id,
            evaluation_id=evaluation.id,
        )

        return {
            "status": "promoted",
            "skill": promoted.skill_name,
            "version": promoted.version,
            "reason": reason,
        }

    # ------------------------------------------------------------------
    # Analytics / regression policy / experiments
    # ------------------------------------------------------------------

    async def record_execution(
        self,
        *,
        skill_name: str,
        skill_version: str,
        trajectory_id: str | None = None,
        success: bool,
        latency_ms: float,
        tokens: int,
        tool_failures: int,
    ) -> str:
        """Store one execution sample for later analytics and A/B reads."""

        return await self.metrics.record(
            skill_name=skill_name,
            skill_version=skill_version,
            trajectory_id=trajectory_id,
            success=success,
            latency_ms=latency_ms,
            tokens=tokens,
            tool_failures=tool_failures,
        )

    async def skill_analytics(
        self,
        skill_name: str,
        *,
        version: str | None = None,
    ) -> dict[str, Any]:
        """Aggregate metrics plus regression history and experiments."""

        summary = await self.analytics.summary(skill_name, version)
        summary["regressions"] = await self.rollback_policy.history(skill_name)
        summary["experiments"] = await self.experiments.list(skill_name)

        return summary

    async def create_experiment(
        self,
        *,
        skill_name: str,
        control_version: str,
        experiment_version: str,
        traffic_percent: int,
    ) -> dict[str, Any]:
        """Open an A/B split, refusing unknown versions."""

        for version in (control_version, experiment_version):
            record = await self.repository.get_version(skill_name, version)

            if record is None:
                raise ValueError(
                    f"skill version {skill_name}@{version} not found"
                )

        return await self.experiments.create(
            skill_name=skill_name,
            control_version=control_version,
            experiment_version=experiment_version,
            traffic_percent=traffic_percent,
        )

    async def choose_skill_version(self, skill_name: str) -> str | None:
        """Version to serve for this execution, or None without an experiment."""

        return await self.experiments.choose_version(skill_name)

    async def enforce_rollback(
        self,
        skill_name: str,
        *,
        stable_version: str,
        current_version: str,
    ) -> dict[str, Any]:
        """Run the automatic rollback policy for one version pair."""

        return await self.rollback_policy.evaluate(
            skill_name,
            stable_version,
            current_version,
        )


def build_skills_service(
    settings: Settings,
    memory: "MemoryProvider",
    personal_tools: "PersonalToolProvider",
) -> SkillsService:
    """Construct the skills service used by the API and keep it on app.state."""
    return SkillsService(settings, memory, personal_tools)
