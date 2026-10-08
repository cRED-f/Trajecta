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
    SkillAnalyticsService,
    SkillExecutionAttributor,
    SkillMetricsCollector,
)
from server.src.skills.dependencies import SkillDependencyGraph
from server.src.skills.evaluation.evaluator import SkillEvaluator
from server.src.skills.evaluation.fixtures import ReplayFixtureStore
from server.src.skills.evaluation.replay import DeepAgentReplayExecutor, SkillReplay
from server.src.skills.evaluation.regression import SkillRegressionDetector
from server.src.skills.experiments import SkillExperimentRouter, SkillExperimentService
from server.src.skills.learning import SkillLearningCoordinator
from server.src.skills.promotion.promoter import SkillPromoter
from server.src.skills.regression import (
    AutomaticRollback,
    SkillRegressionMonitor,
    VersionRegressionDetector,
)
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
        # Built first: activation refuses to go live while a required
        # dependency is missing, and the graph reads the same repository.
        self.dependencies = SkillDependencyGraph(memory.sqlite, repository)

        self.versioner = SkillVersioner(
            repository=repository,
            procedural=ProceduralMemory(memory),
            dependencies=self.dependencies,
        )
        self.promoter = SkillPromoter(
            repository=repository,
            versioner=self.versioner,
        )

        # -- Analytics / experiments -----------------------------------
        # ``summary`` is the older aggregate over recorded samples; the
        # read side over live execution rows is ``analytics``, and it is
        # what experiments and the monitor both read. Built before the
        # learning loop because the loop starts upgrades as experiments.
        self.metrics = SkillMetricsCollector(memory.sqlite)
        self.summary = SkillAnalytics(memory.sqlite)
        self.analytics = SkillAnalyticsService(memory.sqlite)

        self.experiments = SkillExperimentService(
            settings=settings,
            db=memory.sqlite,
            repository=repository,
            promoter=self.promoter,
            versioner=self.versioner,
            analytics=self.analytics,
            trajectories=trajectories,
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
            repository=repository,
            experiments=self.experiments,
        )

        # -- Regression policy ----------------------------------------
        # Legacy single-pair A/B split, still behind POST /experiments.
        # Newer multi-arm experiments go through ``experiments`` above.
        self.router = SkillExperimentRouter(memory.sqlite)

        self.detector = SkillRegressionDetector()
        self.version_regression = VersionRegressionDetector(
            analytics=self.summary,
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

        self.regression = SkillRegressionMonitor(
            settings=settings,
            db=memory.sqlite,
            repository=repository,
            analytics=self.analytics,
            promoter=self.promoter,
            versioner=self.versioner,
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
            # Keep the gate itself intact, but hand the caller the evidence
            # instead of one opaque code: the gate reasons first, then the
            # held-out cases that actually failed.
            reasons = list(evaluation.comparison.reasons)

            for case in evaluation.case_results:
                candidate = case.get("candidate")

                if not isinstance(candidate, dict):
                    continue

                if (
                    candidate.get("success") is True
                    and not candidate.get("skipped")
                ):
                    continue

                detail = candidate.get("error") or candidate.get("judge_reason")

                if not detail:
                    continue

                message = f"{case.get('case_id', 'unknown')}: {detail}"[:300]

                if message not in reasons:
                    reasons.append(message)

                if len(reasons) >= 6:
                    break

            if (
                not reasons
                and not evaluation.comparison.improvement_pass
            ):
                reasons.append(
                    "Candidate did not demonstrate the required "
                    "improvement over baseline."
                )

            if not reasons:
                reasons.append(f"Evaluation verdict: {evaluation.verdict}.")

            return {
                "status": "rejected",
                "reason": (
                    "evaluation_needs_review"
                    if evaluation.verdict == "needs_review"
                    else "evaluation_failed"
                ),
                "evaluation": evaluation.id,
                "verdict": evaluation.verdict,
                "reasons": reasons,
                "report": evaluation.model_dump(mode="json"),
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
        """Versions, experiments and regressions for one skill, with metrics.

        The dashboard is the primary shape; the older one-version aggregate
        is folded in alongside it so callers that only want a success rate
        still get one without a second round trip.
        """

        dashboard = await self.analytics.skill_dashboard(skill_name)

        for key, value in (await self.summary.summary(skill_name, version)).items():
            if key != "versions":
                dashboard[key] = value

        dashboard["rollback_history"] = await self.rollback_policy.history(
            skill_name
        )

        return dashboard

    async def complete_runtime_trajectory(
        self,
        trajectory_id: str,
        *,
        success: bool,
        metrics: dict[str, Any] | None = None,
    ) -> None:
        """Close out live metrics, then let experiments and monitoring react.

        Order matters: a run that has just finished may be the sample that
        tips an experiment or exposes a regression, so both are checked here
        rather than on the next dashboard visit.
        """

        metrics = metrics or {}

        completed = await self.analytics.complete_trajectory(
            trajectory_id,
            success=success,
            duration_seconds=float(metrics.get("duration_seconds") or 0.0),
            input_tokens=int(metrics.get("input_tokens") or 0),
            output_tokens=int(metrics.get("output_tokens") or 0),
            tool_calls=int(metrics.get("tool_calls") or 0),
            tool_errors=int(metrics.get("tool_errors") or 0),
        )

        experiment_ids = {
            str(row["experiment_id"])
            for row in completed
            if row.get("experiment_id")
        }

        for experiment_id in experiment_ids:
            await self.experiments.maybe_auto_stop(experiment_id)

        # Experiment observations are handled above. Only normal active-skill
        # exposures feed post-promotion regression monitoring.
        skill_names = {
            str(row["skill_name"])
            for row in completed
            if not row.get("experiment_id") and row.get("arm_kind") == "active"
        }

        for skill_name in skill_names:
            await self.regression.check(skill_name)

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

        return await self.router.create(
            skill_name=skill_name,
            control_version=control_version,
            experiment_version=experiment_version,
            traffic_percent=traffic_percent,
        )

    async def choose_skill_version(self, skill_name: str) -> str | None:
        """Version to serve for this execution, or None without an experiment."""

        return await self.router.choose_version(skill_name)

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
