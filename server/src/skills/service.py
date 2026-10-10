"""Skill persistence, task trajectories, replay evaluation and promotion.

Automatic discovery is managed separately by the durable task-learning worker;
this service retains verification and permission-safe version lifecycle.
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
from server.src.skills.evaluation.live_stream import BackgroundEvaluationStreams
from server.src.skills.evaluation.replay import DeepAgentReplayExecutor, SkillReplay
from server.src.skills.evaluation.regression import SkillRegressionDetector
from server.src.skills.experiments import SkillExperimentRouter, SkillExperimentService
from server.src.skills.promotion.promoter import SkillPromoter
from server.src.skills.regression import (
    AutomaticRollback,
    SkillRegressionMonitor,
    VersionRegressionDetector,
)
from server.src.skills.repository import SkillRepository
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
        self._settings = settings
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
        self.active_ui_evaluations: set[str] = set()
        self.background_evaluation_streams = BackgroundEvaluationStreams()

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
        evaluation_id: str | None = None,
        model_name: str | None = None,
    ) -> dict[str, Any]:
        """Explicit manual upgrade with strict evaluation-based promotion.

        This method is an explicit upgrade action, never part of the ordinary
        experience-learning/chat loop. Reuse a provided evaluation, otherwise
        run one targeted manual evaluation. Never promote a failed report.
        """
        if evaluation_id is not None:
            report = await self.repository.get_evaluation(evaluation_id)
            if report is None:
                raise ValueError(f"evaluation {evaluation_id!r} not found")
            if str(report.get("candidate_id")) != candidate_id:
                raise ValueError("evaluation does not belong to candidate")
        else:
            report = await self.evaluator.evaluate(
                candidate_id, model_name=model_name,
            )

        def field(obj: Any, name: str, default: Any = None) -> Any:
            return obj.get(name, default) if isinstance(obj, dict) else getattr(obj, name, default)

        verdict = str(field(report, "verdict", "needs_review"))
        report_id = str(field(report, "id"))
        if verdict != "pass":
            comparison = field(report, "comparison", {})
            reasons = list(field(comparison, "reasons", []) or [])
            for case in field(report, "case_results", []) or []:
                candidate = field(case, "candidate", {})
                if field(candidate, "skipped") or field(candidate, "success"):
                    continue
                case_id = field(case, "case_id", "unknown")
                message = field(candidate, "judge_reason", None)
                if message:
                    reasons.append(f"{case_id}: {message}")
            payload = (
                report if isinstance(report, dict)
                else report.model_dump(mode="json")
            )
            return {
                "status": "rejected",
                "reason": "evaluation_needs_review" if verdict == "needs_review" else "evaluation_failed",
                "evaluation": report_id,
                "verdict": verdict,
                "reasons": reasons,
                "report": payload,
            }

        promoted = await self.promoter.promote(
            candidate_id=candidate_id, evaluation_id=report_id,
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
        success: bool | None,
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

        # Unverified chat completions contribute latency and cost metrics,
        # never experiment winners or automatic regression decisions.
        if success is None:
            return

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
        """No legacy traffic splitting unless experiments are explicitly enabled."""
        if not self._settings.skills.experiments.enabled:
            return None
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
