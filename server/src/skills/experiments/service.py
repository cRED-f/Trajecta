"""Live experiments between skill versions.

An experiment arms a staged version against whatever is active today and
routes each conversation to one of them. Assignment is sticky per unit
(the thread), so the same conversation never flips arms halfway through,
and every automatic decision requires the frequentist and Bayesian
readings to agree before a version is swapped in for everyone.
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import uuid

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from server.src.config import SkillExperimentConfig
from server.src.memory.storage.sqlite import SQLiteDatabase
from server.src.skills.analytics import SkillAnalyticsService
from server.src.skills.experiments.statistics import (
    BinaryArm,
    PairwiseEvidence,
    pairwise_evidence,
)
from server.src.skills.promotion.promoter import SkillPromoter
from server.src.skills.repository import SkillRepository
from server.src.skills.representation.skill import Skill, SkillStatus
from server.src.skills.versioning.versioner import SkillVersioner

logger = logging.getLogger(__name__)

# The arm that means "behave as if the skill does not exist", for testing
# a brand-new skill against ordinary agent behaviour.
BASELINE_VERSION = "__baseline__"

# Terminal experiment statuses.
RUNNING = "running"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _loads(value: Any, default: Any) -> Any:
    if not isinstance(value, str) or not value:
        return default

    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


@dataclass(slots=True, frozen=True)
class ExperimentAssignment:
    """One skill, one chosen version, for one conversation."""

    experiment_id: str | None
    skill_name: str
    version: str
    arm_kind: str
    unit_id: str
    prompt_override: str | None = None

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)

        # The experimental prompt can be thousands of tokens; it belongs in
        # the agent's context, not in event metadata sent to the frontend.
        value.pop("prompt_override", None)

        return value


class SkillExperimentService:
    def __init__(
        self,
        *,
        db: SQLiteDatabase,
        repository: SkillRepository,
        promoter: SkillPromoter,
        versioner: SkillVersioner,
        analytics: SkillAnalyticsService,
        settings: Any | None = None,
        config: SkillExperimentConfig | None = None,
        trajectories: Any | None = None,
    ) -> None:
        if config is None and settings is not None:
            config = settings.skills.experiments

        if config is None:
            config = SkillExperimentConfig()

        self._db = db
        self._config = config
        self._repository = repository
        self._promoter = promoter
        self._versioner = versioner
        self._analytics = analytics
        self._trajectories = trajectories

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    async def start_candidate(
        self,
        candidate_id: str,
        *,
        strategy: str | None = None,
        traffic_percent: int | None = None,
        auto_stop: bool | None = None,
        auto_promote: bool | None = None,
    ) -> dict[str, Any]:
        """Stage a verified candidate as a treatment arm of a new experiment."""

        if not self._config.enabled:
            raise ValueError("skill experiments are disabled")

        candidate = await self._repository.get_candidate(candidate_id)
        skill = await self._repository.get_candidate_skill(candidate_id)

        if candidate is None or skill is None:
            raise ValueError(f"skill candidate {candidate_id!r} not found")

        if await self._running_experiment(skill.name) is not None:
            raise ValueError(f"skill {skill.name!r} already has a running experiment")

        # Create the immutable version, but do NOT materialize it globally.
        staged = await self._promoter.stage(candidate_id=candidate_id)

        active = await self._repository.get_active(skill.name)

        control_version = (
            str(active["version"])
            if active and active.get("status") == "active"
            else BASELINE_VERSION
        )

        resolved_strategy = strategy or self._config.default_strategy

        if resolved_strategy not in {"ab", "thompson"}:
            raise ValueError("experiment strategy must be 'ab' or 'thompson'")

        experiment_id = uuid.uuid4().hex

        await self._db.execute(
            """
            INSERT INTO skill_experiments(
                id,
                skill_name,
                control_version,
                experiment_version,
                strategy,
                status,
                traffic_percent,
                min_samples_per_arm,
                max_samples_total,
                alpha,
                bayesian_threshold,
                minimum_effect,
                harm_effect,
                bayesian_draws,
                auto_stop,
                auto_promote,
                created_at,
                metadata
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                experiment_id,
                skill.name,
                control_version,
                # v13's single-treatment column: the first treatment arm,
                # kept so the legacy router can still read this row.
                staged.version,
                resolved_strategy,
                RUNNING,
                traffic_percent or self._config.treatment_traffic_percent,
                self._config.min_samples_per_arm,
                self._config.max_samples_total,
                self._config.alpha,
                self._config.bayesian_threshold,
                self._config.minimum_success_effect,
                self._config.harm_effect,
                self._config.bayesian_draws,
                int(self._config.auto_stop if auto_stop is None else auto_stop),
                int(
                    self._config.auto_promote_winner
                    if auto_promote is None
                    else auto_promote
                ),
                _now(),
                json.dumps(
                    {"source_candidate_id": candidate_id}, ensure_ascii=False
                ),
            ),
        )

        await self._add_arm_record(
            experiment_id=experiment_id,
            version=control_version,
            candidate_id=None,
            is_control=True,
        )

        await self._add_arm_record(
            experiment_id=experiment_id,
            version=staged.version,
            candidate_id=candidate_id,
            is_control=False,
        )

        await self._repository.update_candidate(
            candidate_id,
            status=SkillStatus.EXPERIMENTING,
            extra_metadata={
                "experiment_id": experiment_id,
                "staged_version": staged.version,
            },
        )

        return await self.get(experiment_id) or {}

    async def add_candidate_arm(
        self, experiment_id: str, candidate_id: str
    ) -> dict[str, Any]:
        """Stage another verified candidate into an existing experiment."""

        experiment = await self.get(experiment_id)

        if experiment is None:
            raise ValueError("experiment not found")

        if experiment["status"] != RUNNING:
            raise ValueError("only running experiments can accept new arms")

        candidate = await self._repository.get_candidate(candidate_id)
        skill = await self._repository.get_candidate_skill(candidate_id)

        if candidate is None or skill is None:
            raise ValueError("candidate not found")

        if skill.name != experiment["skill_name"]:
            raise ValueError(
                "candidate skill name does not match experiment skill"
            )

        staged = await self._promoter.stage(candidate_id=candidate_id)

        await self._add_arm_record(
            experiment_id=experiment_id,
            version=staged.version,
            candidate_id=candidate_id,
            is_control=False,
        )

        await self._repository.update_candidate(
            candidate_id,
            status=SkillStatus.EXPERIMENTING,
            extra_metadata={
                "experiment_id": experiment_id,
                "staged_version": staged.version,
            },
        )

        return await self.get(experiment_id) or {}

    async def stop(
        self,
        experiment_id: str,
        *,
        reason: str = "manual stop",
        winner_version: str | None = None,
    ) -> dict[str, Any]:
        """End an experiment and release its treatment candidates.

        Releasing matters: candidates left in ``experimenting`` can never be
        promoted by anything else, so an abandoned experiment would strand
        them forever.
        """

        experiment = await self.get(experiment_id)

        if experiment is None:
            raise ValueError("experiment not found")

        if experiment["status"] == RUNNING:
            await self._release_treatment_arms(
                experiment_id,
                result="manual_stop",
                candidate_status="verified",
            )

            await self._db.execute(
                """
                UPDATE skill_experiments
                SET status = 'stopped',
                    completed_at = ?,
                    reason = ?,
                    winner_version = ?
                WHERE id = ?
                """,
                (_now(), reason, winner_version, experiment_id),
            )

        return await self.get(experiment_id) or {}

    # ------------------------------------------------------------------
    # assignment
    # ------------------------------------------------------------------

    async def prepare_task(
        self, *, task_text: str, unit_id: str
    ) -> list[ExperimentAssignment]:
        """Assign every running experiment this task is plausibly about.

        Irrelevant experiments are skipped rather than assigned at random:
        injecting a skill variant into a task that never needed the skill
        would only add noise to the comparison.
        """

        if not self._config.enabled:
            return []

        experiments = await self._db.fetch(
            f"""
            SELECT * FROM skill_experiments
            WHERE status = '{RUNNING}'
            ORDER BY created_at ASC
            """
        )

        assignments: list[ExperimentAssignment] = []

        for experiment in experiments:
            skill_name = str(experiment["skill_name"])
            skill = await self._load_skill_for_experiment(skill_name, experiment)

            if skill is None:
                continue

            if self._relevance(task_text, skill) < self._config.relevance_threshold:
                continue

            version = await self._assignment_for(experiment, unit_id)

            try:
                prompt_override = await self._prompt_override(skill_name, version)
            except ValueError as exc:
                # A staged arm whose bundle is gone must not break the run —
                # skip it rather than mislabeling the control as treatment.
                logger.warning(
                    "skipping experiment arm %s@%s: %s", skill_name, version, exc
                )
                continue

            assignments.append(
                ExperimentAssignment(
                    experiment_id=str(experiment["id"]),
                    skill_name=skill_name,
                    version=version,
                    arm_kind=(
                        "control"
                        if version == str(experiment["control_version"])
                        else "treatment"
                    ),
                    unit_id=unit_id,
                    prompt_override=prompt_override,
                )
            )

        return assignments

    async def bind_trajectory(
        self, trajectory_id: str, assignments: list[ExperimentAssignment]
    ) -> None:
        """Record which arms applied to a run, for post-hoc attribution.

        The metrics row needs to know which experiment a sample belongs to,
        and that can only be answered while the run is still live.
        """

        if not assignments or self._trajectories is None:
            return

        await self._trajectories.append(
            trajectory_id,
            event_type="skill.assignments",
            data={"assignments": [item.to_dict() for item in assignments]},
            source="main",
        )

    async def _assignment_for(self, experiment: dict[str, Any], unit_id: str) -> str:
        existing = await self._db.fetchone(
            """
            SELECT version FROM skill_experiment_assignments
            WHERE experiment_id = ? AND unit_id = ?
            """,
            (str(experiment["id"]), unit_id),
        )

        if existing is not None:
            return str(existing["version"])

        arms = await self._db.fetch(
            """
            SELECT * FROM skill_experiment_arms
            WHERE experiment_id = ?
            ORDER BY is_control DESC, created_at ASC
            """,
            (str(experiment["id"]),),
        )

        if not arms:
            raise ValueError("experiment has no arms")

        if str(experiment["strategy"]) == "thompson":
            version = await self._choose_thompson(experiment, arms, unit_id)
        else:
            version = self._choose_ab(experiment, arms, unit_id)

        await self._db.execute(
            """
            INSERT OR IGNORE INTO skill_experiment_assignments(
                experiment_id,
                unit_id,
                version,
                assigned_at
            )
            VALUES(?, ?, ?, ?)
            """,
            (str(experiment["id"]), unit_id, version, _now()),
        )

        return version

    def _choose_ab(
        self,
        experiment: dict[str, Any],
        arms: list[dict[str, Any]],
        unit_id: str,
    ) -> str:
        control = next(
            (str(item["version"]) for item in arms if item["is_control"]),
            None,
        )

        treatments = [str(item["version"]) for item in arms if not item["is_control"]]

        if control is None or not treatments:
            return str(arms[0]["version"])

        # Deterministic assignment means the same conversation does not
        # bounce between control and treatment.
        digest = hashlib.sha256(
            f"{experiment['id']}:{unit_id}".encode()
        ).digest()

        bucket = int.from_bytes(digest[:8], "big") % 100

        if bucket >= int(experiment["traffic_percent"]):
            return control

        treatment_index = int.from_bytes(digest[8:16], "big") % len(treatments)

        return treatments[treatment_index]

    async def _choose_thompson(
        self,
        experiment: dict[str, Any],
        arms: list[dict[str, Any]],
        unit_id: str,
    ) -> str:
        digest = hashlib.sha256(f"{experiment['id']}:{unit_id}".encode()).digest()
        rng = random.Random(int.from_bytes(digest[:8], "big"))

        best_version = str(arms[0]["version"])
        best_sample = -1.0

        for arm in arms:
            version = str(arm["version"])

            summary = await self._analytics.version_summary(
                str(experiment["skill_name"]),
                version,
                experiment_id=str(experiment["id"]),
            )

            successes = int(summary["successes"])
            failures = int(summary["failures"])

            # Posterior Beta(successes+1, failures+1)
            sample = rng.betavariate(successes + 1, failures + 1)

            if sample > best_sample:
                best_sample = sample
                best_version = version

        return best_version

    async def _prompt_override(self, skill_name: str, version: str) -> str | None:
        """System-prompt text steering this run to one version of a skill.

        The skill directory is never swapped: the active bundle stays put
        for everyone else, and only the assigned run is told which version
        to follow.
        """

        active = await self._repository.get_active(skill_name)

        active_version = (
            str(active["version"])
            if active and active.get("status") == "active"
            else None
        )

        if version == active_version:
            return None

        if version == BASELINE_VERSION:
            return (
                "\n"
                "<trajecta-skill-experiment "
                f'skill="{skill_name}" version="baseline">\n'
                "\n"
                "For this run, do not use the verified skill "
                f"named {skill_name!r}. "
                "\n"
                "Solve the task using normal agent behavior. "
                "This overrides any materialized version of "
                "that skill.\n"
                "\n"
                "</trajecta-skill-experiment>\n"
            )

        skill = await self._repository.get_version_skill(skill_name, version)

        if skill is None:
            raise ValueError(f"experiment version {skill_name}@{version} is missing")

        steps = "\n".join(
            f"{index + 1}. {step.instruction}"
            for index, step in enumerate(skill.workflow.steps)
        )

        return (
            "\n"
            "<trajecta-skill-experiment "
            f'skill="{skill_name}" version="{version}">\n'
            "\n"
            "For this run, this is the selected version "
            f"of skill {skill_name!r}. "
            "\n"
            "Use it instead of any other version "
            f"of that skill under /skills/.\n"
            "\n"
            f"Description: {skill.description}\n"
            f"Trigger: {skill.workflow.trigger}\n"
            "\n"
            f"Instructions:\n{skill.instructions}\n"
            "\n"
            f"Workflow:\n{steps}\n"
            "\n"
            "</trajecta-skill-experiment>\n"
        )

    # ------------------------------------------------------------------
    # reading and deciding
    # ------------------------------------------------------------------

    async def get(self, experiment_id: str) -> dict[str, Any] | None:
        row = await self._db.fetchone(
            "SELECT * FROM skill_experiments WHERE id = ?", (experiment_id,)
        )

        if row is None:
            return None

        return await self._decorate(row)

    async def list(self, *, skill_name: str | None = None) -> list[dict[str, Any]]:
        if skill_name:
            rows = await self._db.fetch(
                """
                SELECT * FROM skill_experiments
                WHERE skill_name = ?
                ORDER BY created_at DESC
                """,
                (skill_name,),
            )
        else:
            rows = await self._db.fetch(
                "SELECT * FROM skill_experiments ORDER BY created_at DESC"
            )

        return [await self._decorate(row) for row in rows]

    async def analysis(self, experiment_id: str) -> dict[str, Any]:
        """Evidence per arm, plus the auto-stop decision if one is due."""

        experiment = await self.get(experiment_id)

        if experiment is None:
            raise ValueError("experiment not found")

        arms = await self._arms(experiment_id)
        skill_name = str(experiment["skill_name"])
        control_version = str(experiment["control_version"])

        summaries: dict[str, dict[str, Any]] = {}

        for arm in arms:
            version = str(arm["version"])
            summaries[version] = await self._analytics.version_summary(
                skill_name, version, experiment_id=experiment_id
            )

        control_summary = summaries.get(control_version) or self._empty_summary(
            skill_name, control_version
        )

        control_arm = BinaryArm(
            version=control_version,
            successes=int(control_summary["successes"]),
            total=int(control_summary["total"]),
        )

        evidence_rows: list[dict[str, Any]] = []
        treatment_evidence: list[tuple[str, PairwiseEvidence]] = []

        minimum_effect = float(experiment["minimum_effect"])
        harm_effect = float(experiment["harm_effect"])
        alpha = float(experiment["alpha"])
        bayesian_threshold = float(experiment["bayesian_threshold"])

        for version, summary in summaries.items():
            if version == control_version:
                evidence_rows.append(
                    {"version": version, "summary": summary, "evidence": None}
                )
                continue

            arm = BinaryArm(
                version=version,
                successes=int(summary["successes"]),
                total=int(summary["total"]),
            )

            evidence = pairwise_evidence(
                control_arm,
                arm,
                minimum_effect=minimum_effect,
                harm_effect=harm_effect,
                bayesian_draws=int(experiment["bayesian_draws"]),
                seed_key=f"{experiment_id}:{version}",
            )

            treatment_evidence.append((version, evidence))

            evidence_rows.append(
                {"version": version, "summary": summary, "evidence": evidence}
            )

        total_samples = sum(int(item["total"]) for item in summaries.values())

        decision = self._decide(
            experiment=experiment,
            control_version=control_version,
            treatment_evidence=treatment_evidence,
            summaries=summaries,
            total_samples=total_samples,
            minimum_effect=minimum_effect,
            harm_effect=harm_effect,
            alpha=alpha,
            bayesian_threshold=bayesian_threshold,
        )

        auto_stopped = False
        auto_promoted = False
        status = str(experiment["status"])

        if decision["decision"] != "continue" and bool(experiment["auto_stop"]):
            if experiment["status"] == RUNNING:
                stopped = await self.stop(
                    experiment_id,
                    reason=str(decision["reason"]),
                    winner_version=decision["winner_version"],
                )
                auto_stopped = True
                # The response must not claim "running" for an experiment
                # this very call just closed.
                status = str(stopped.get("status") or status)

            winner = decision["winner_version"]

            if (
                winner
                and winner != control_version
                and bool(experiment["auto_promote"])
            ):
                try:
                    await self._versioner.activate_version(skill_name, winner)
                    auto_promoted = True
                except Exception:
                    # A failed activation leaves the experiment stopped and
                    # the winner recorded; it must not escape into the API.
                    pass

        return {
            "experiment_id": experiment_id,
            "skill_name": skill_name,
            "strategy": experiment["strategy"],
            "status": status,
            "control_version": control_version,
            "total_samples": total_samples,
            "decision": decision["decision"],
            "winner_version": decision["winner_version"],
            "reason": decision["reason"],
            "arms": evidence_rows,
            "auto_stopped": auto_stopped,
            "auto_promoted": auto_promoted,
        }

    async def maybe_auto_stop(self, experiment_id: str) -> dict[str, Any] | None:
        """Analyses and stops only when this experiment may end itself.

        Called after every completed run so a verdict lands on the very
        sample that produced it, rather than on the next page view.
        """

        experiment = await self.get(experiment_id)

        if experiment is None or experiment["status"] != RUNNING:
            return None

        if not bool(experiment["auto_stop"]):
            return None

        result = await self.analysis(experiment_id)

        if result["decision"] == "continue":
            return None

        return result

    def _decide(
        self,
        *,
        experiment: dict[str, Any],
        control_version: str,
        treatment_evidence: list[tuple[str, PairwiseEvidence]],
        summaries: dict[str, dict[str, Any]],
        total_samples: int,
        minimum_effect: float,
        harm_effect: float,
        alpha: float,
        bayesian_threshold: float,
    ) -> dict[str, Any]:
        """Terminal verdict only when both the z-test and the posterior agree.

        Multi-arm winners must additionally beat every competing treatment,
        so the last arm standing is genuinely better rather than merely
        better than control.
        """

        winner: str | None = None
        decision = "continue"
        reason: str | None = None

        strong_winners = [
            (version, evidence)
            for version, evidence in treatment_evidence
            if evidence.delta >= minimum_effect
            and evidence.p_superiority <= alpha
            and evidence.bayes_superiority >= bayesian_threshold
        ]

        strong_winners.sort(key=lambda item: item[1].treatment_rate, reverse=True)

        if strong_winners:
            candidate_version, _candidate_evidence = strong_winners[0]
            candidate_summary = summaries[candidate_version]

            candidate_arm = BinaryArm(
                version=candidate_version,
                successes=int(candidate_summary["successes"]),
                total=int(candidate_summary["total"]),
            )

            beats_every_other_treatment = True

            for other_version, other_summary in summaries.items():
                if other_version in {control_version, candidate_version}:
                    continue

                other_arm = BinaryArm(
                    version=other_version,
                    successes=int(other_summary["successes"]),
                    total=int(other_summary["total"]),
                )

                head_to_head = pairwise_evidence(
                    other_arm,
                    candidate_arm,
                    minimum_effect=minimum_effect,
                    harm_effect=harm_effect,
                    bayesian_draws=int(experiment["bayesian_draws"]),
                    seed_key=f"{experiment['id']}:{candidate_version}:vs:{other_version}",
                )

                if not (
                    head_to_head.delta >= minimum_effect
                    and head_to_head.p_superiority <= alpha
                    and head_to_head.bayes_superiority >= bayesian_threshold
                ):
                    beats_every_other_treatment = False
                    break

            if beats_every_other_treatment:
                winner = candidate_version
                decision = "treatment_wins"
                reason = (
                    "winner passed frequentist and Bayesian superiority "
                    "against control and all competing treatments"
                )

        if winner is None and treatment_evidence:
            all_harmful = all(
                evidence.delta <= -harm_effect
                and evidence.p_harm <= alpha
                and evidence.bayes_harm >= bayesian_threshold
                for _version, evidence in treatment_evidence
            )

            if all_harmful:
                winner = control_version
                decision = "control_wins"
                reason = "all treatments show statistically supported harm"

        if winner is None and total_samples >= int(experiment["max_samples_total"]):
            decision = "inconclusive"
            reason = "maximum sample budget reached without sufficient evidence"

        return {
            "decision": decision,
            "winner_version": winner,
            "reason": reason,
        }

    # ------------------------------------------------------------------
    # storage helpers
    # ------------------------------------------------------------------

    async def _add_arm_record(
        self,
        *,
        experiment_id: str,
        version: str,
        candidate_id: str | None,
        is_control: bool,
    ) -> None:
        await self._db.execute(
            """
            INSERT OR IGNORE INTO skill_experiment_arms(
                experiment_id,
                version,
                candidate_id,
                is_control,
                created_at
            )
            VALUES(?, ?, ?, ?, ?)
            """,
            (
                experiment_id,
                version,
                candidate_id,
                int(is_control),
                _now(),
            ),
        )

    async def _release_treatment_arms(
        self,
        experiment_id: str,
        *,
        result: str = "stopped",
        candidate_status: str = "verified",
    ) -> None:
        """Return staged candidates to ``verified`` so nothing stays stuck.

        An experiment that is stopped — manually or by reaching a verdict —
        must not be the reason a candidate can never be promoted.
        """

        arms = await self._db.fetch(
            """
            SELECT candidate_id FROM skill_experiment_arms
            WHERE experiment_id = ? AND is_control = 0
            """,
            (experiment_id,),
        )

        for arm in arms:
            candidate_id = arm.get("candidate_id")

            if not candidate_id:
                continue

            try:
                await self._repository.update_candidate(
                    str(candidate_id),
                    status=candidate_status,
                    extra_metadata={
                        "experiment_result": result,
                        "experiment_id": experiment_id,
                    },
                )
            except ValueError:
                # The candidate may have been removed since it was staged.
                continue

    async def _running_experiment(self, skill_name: str) -> dict[str, Any] | None:
        return await self._db.fetchone(
            """
            SELECT * FROM skill_experiments
            WHERE skill_name = ? AND status = ?
            """,
            (skill_name, RUNNING),
        )

    async def _arms(self, experiment_id: str) -> list[dict[str, Any]]:
        return await self._db.fetch(
            """
            SELECT * FROM skill_experiment_arms
            WHERE experiment_id = ?
            ORDER BY is_control DESC, created_at ASC
            """,
            (experiment_id,),
        )

    async def _decorate(self, row: dict[str, Any]) -> dict[str, Any]:
        value = dict(row)
        value["metadata"] = _loads(value.get("metadata"), {})

        for flag in ("auto_stop", "auto_promote"):
            value[flag] = bool(value.get(flag))

        value["arms"] = await self._analytics.experiment_arms(str(value["id"]))

        return value

    async def _load_skill_for_experiment(
        self, skill_name: str, experiment: dict[str, Any]
    ) -> Skill | None:
        """The control version's skill, for judging whether a task fits."""

        control_version = str(experiment.get("control_version") or "")

        if control_version and control_version != BASELINE_VERSION:
            skill = await self._repository.get_version_skill(
                skill_name, control_version
            )

            if skill is not None:
                return skill

        active = await self._repository.get_active(skill_name)

        if active:
            skill = await self._repository.get_version_skill(
                skill_name, str(active["version"])
            )

            if skill is not None:
                return skill

        return None

    @staticmethod
    def _relevance(task_text: str, skill: Skill) -> float:
        """Word overlap between the task and what the skill says it handles.

        Cheap on purpose: this runs on every prepare(), and a false
        negative only costs an experiment a sample.
        """

        task = set(task_text.lower().split())
        subject = set(
            f"{skill.workflow.trigger} {skill.description}".lower().split()
        )

        if not task or not subject:
            return 0.0

        return len(task & subject) / len(task | subject)

    @staticmethod
    def _empty_summary(skill_name: str, version: str) -> dict[str, Any]:
        return {
            "skill_name": skill_name,
            "version": version,
            "total": 0,
            "successes": 0,
            "failures": 0,
            "success_rate": 0.0,
            "average_duration_seconds": 0.0,
            "average_input_tokens": 0.0,
            "average_output_tokens": 0.0,
            "average_total_tokens": 0.0,
            "average_tool_calls": 0.0,
            "average_tool_errors": 0.0,
            "tool_error_rate": 0.0,
        }
