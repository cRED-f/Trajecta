"""Automatic regression rollback between a live version and its stable one.

A single failed task says nothing about a skill: flipping ``/skills/`` on one
bad run would thrash the materialized bundle and hide a real regression
behind a constant stream of rollbacks. This monitor waits for a sample
window of recorded executions on both sides and then decides:

* A success-rate drop only trips when the two-proportion z-test (``alpha``)
  and the Beta-Binomial posterior (``bayesian_harm_threshold``) *agree* the
  new version is worse — the same dual-reading rule the A/B experiments
  use, so a lucky streak on a handful of samples cannot undo a promotion.
* Latency blow-ups (``maximum_latency_ratio``) and tool-error increases
  (``maximum_tool_error_rate_increase``) trip on their own as operational
  safety signals, even when the success rate is unchanged.

Every detection is written to ``skill_regressions`` *before* the rollback is
attempted, so a failed restore still leaves a durable trace. The failure is
recorded in ``rollback_error`` and never raised: this runs from analytics
and learning paths that must not break their caller.

This supersedes ``VersionRegressionDetector`` / ``AutomaticRollback``, which
compare two versions with fixed class-level thresholds and no statistics.
"""

from __future__ import annotations

import json
import uuid

from datetime import UTC, datetime
from typing import Any

from server.src.config import Settings, SkillRegressionConfig
from server.src.memory.storage.sqlite import SQLiteDatabase
from server.src.skills.analytics.service import SkillAnalyticsService
from server.src.skills.experiments.statistics import (
    BinaryArm,
    PairwiseEvidence,
    pairwise_evidence,
)
from server.src.skills.promotion.promoter import SkillPromoter
from server.src.skills.repository import SkillRepository
from server.src.skills.versioning.versioner import SkillVersioner

# Matches SkillExperimentConfig.bayesian_draws so a regression reading and an
# experiment reading of the same samples are computed the same way.
BAYESIAN_DRAWS = 5000


def _now() -> str:
    return datetime.now(UTC).isoformat()


class SkillRegressionMonitor:
    """Flag a live version that regressed against its stable predecessor."""

    def __init__(
        self,
        *,
        db: SQLiteDatabase,
        repository: SkillRepository,
        analytics: SkillAnalyticsService,
        settings: Settings | None = None,
        config: SkillRegressionConfig | None = None,
        promoter: SkillPromoter | None = None,
        versioner: SkillVersioner | None = None,
    ) -> None:
        """Bind the config and the collaborators ``check`` needs.

        ``config`` wins over ``settings`` so a caller can hand in a tuned
        config directly; service wiring passes ``settings`` and reads
        ``settings.skills.regression`` out of it.

        Either collaborator can perform the rollback: the versioner is
        preferred when both are given because ``SkillVersioner.rollback``
        takes the ``reason`` keyword and records it on the version metadata.
        """

        resolved = config

        if resolved is None and settings is not None:
            resolved = settings.skills.regression

        if resolved is None:
            raise ValueError("SkillRegressionMonitor requires settings or config")

        self._config = resolved

        self._db = db
        self._repository = repository
        self._analytics = analytics
        self._promoter = promoter
        self._versioner = versioner

    async def check(
        self,
        skill_name: str,
        *,
        stable_version: str | None = None,
        current_version: str | None = None,
    ) -> dict[str, Any]:
        """Compare the live version against its stable predecessor once.

        Both versions are optional: when either is omitted the pair is read
        from the active skill row (``version`` and
        ``metadata.previous_version``), which is how the learning loop calls
        it after a promotion. Passing them explicitly checks a pair that is
        not (or no longer) the active one.
        """

        if not self._config.enabled:
            return {"checked": False, "reason": "regression monitoring disabled"}

        current = current_version
        stable = stable_version

        if current is None or stable is None:
            active = await self._repository.get_active(skill_name)

            if active is None or str(active.get("status")) != "active":
                return {"checked": False, "reason": "skill is not active"}

            if current is None:
                current = str(active["version"])

            if stable is None:
                metadata = active.get("metadata") or {}
                stable = str(metadata.get("previous_version") or "")

        if not stable or stable == current:
            return {"checked": False, "reason": "no previous stable version"}

        # Do not attempt a second restore of a version the policy already
        # rolled back; the learning loop re-checks after every promotion.
        already = await self._db.fetchone(
            """
            SELECT id FROM skill_regressions
            WHERE skill_name = ? AND bad_version = ? AND rolled_back = 1
            LIMIT 1
            """,
            (skill_name, current),
        )

        if already is not None:
            return {"checked": False, "reason": "version was already rolled back"}

        window = self._config.window_size

        current_summary = await self._analytics.version_summary(
            skill_name, current, limit=window
        )
        stable_summary = await self._analytics.version_summary(
            skill_name, stable, limit=window
        )

        minimum = self._config.min_samples_per_version

        if (
            int(current_summary["total"]) < minimum
            or int(stable_summary["total"]) < minimum
        ):
            return {
                "checked": True,
                "regression": False,
                "insufficient_data": True,
                "reason": "insufficient online samples",
                "current": current_summary,
                "stable": stable_summary,
            }

        evidence = pairwise_evidence(
            BinaryArm(
                version=stable,
                successes=int(stable_summary["successes"]),
                total=int(stable_summary["total"]),
            ),
            BinaryArm(
                version=current,
                successes=int(current_summary["successes"]),
                total=int(current_summary["total"]),
            ),
            minimum_effect=0.0,
            harm_effect=self._config.success_drop,
            bayesian_draws=BAYESIAN_DRAWS,
            seed_key=f"regression:{skill_name}:{current}",
        )

        reasons = self._reasons(evidence, current_summary, stable_summary)

        evidence_payload = {
            "current": current_summary,
            "stable": stable_summary,
            "pairwise": self._evidence_dict(evidence),
        }

        if not reasons:
            return {
                "checked": True,
                "regression": False,
                "current": current_summary,
                "stable": stable_summary,
                "evidence": evidence_payload,
            }

        severity = (
            "high"
            if "success_rate_regression" in reasons or len(reasons) >= 2
            else "medium"
        )

        event_id = uuid.uuid4().hex

        # Written before the rollback runs: a restore that blows up still
        # leaves a durable, queryable trace of the detection.
        await self._db.execute(
            """
            INSERT INTO skill_regressions(
                id, skill_name, bad_version, stable_version,
                reason, severity, reasons, evidence,
                rolled_back, created_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
            """,
            (
                event_id,
                skill_name,
                current,
                stable,
                # Legacy column: dashboard fallbacks read the first reason.
                reasons[0],
                severity,
                json.dumps(reasons, ensure_ascii=False),
                json.dumps(evidence_payload, ensure_ascii=False),
                _now(),
            ),
        )

        rolled_back = False
        rollback_error: str | None = None

        if self._config.auto_rollback:
            rolled_back, rollback_error = await self._rollback(
                skill_name, stable, reasons
            )

            await self._db.execute(
                """
                UPDATE skill_regressions
                SET rolled_back = ?, rollback_error = ?
                WHERE id = ?
                """,
                (int(rolled_back), rollback_error, event_id),
            )

        return {
            "checked": True,
            "regression": True,
            "event_id": event_id,
            "severity": severity,
            "reasons": reasons,
            "rolled_back": rolled_back,
            "rollback_error": rollback_error,
            "current": current_summary,
            "stable": stable_summary,
            "evidence": evidence_payload,
        }

    def _reasons(
        self,
        evidence: PairwiseEvidence,
        current: dict[str, Any],
        stable: dict[str, Any],
    ) -> list[str]:
        """Every configured signal that fired; empty means "keep the version"."""

        config = self._config
        reasons: list[str] = []

        # Success-rate drop: the effect size, the frequentist reading and the
        # Bayesian posterior must all agree before a promotion is undone.
        if (
            evidence.delta <= -config.success_drop
            and evidence.p_harm <= config.alpha
            and evidence.bayes_harm >= config.bayesian_harm_threshold
        ):
            reasons.append("success_rate_regression")

        stable_latency = float(stable.get("average_duration_seconds") or 0.0)
        current_latency = float(current.get("average_duration_seconds") or 0.0)

        if stable_latency > 0 and (
            current_latency > stable_latency * config.maximum_latency_ratio
        ):
            reasons.append("latency_regression")

        error_increase = float(current.get("tool_error_rate") or 0.0) - float(
            stable.get("tool_error_rate") or 0.0
        )

        if error_increase > config.maximum_tool_error_rate_increase:
            reasons.append("tool_error_regression")

        return reasons

    async def _rollback(
        self,
        skill_name: str,
        version: str,
        reasons: list[str],
    ) -> tuple[bool, str | None]:
        """Restore ``version``. A failure is returned, never raised."""

        reason = "Automatic regression rollback: " + ", ".join(reasons)

        try:
            if self._versioner is not None:
                await self._versioner.rollback(skill_name, version, reason=reason)
            elif self._promoter is not None:
                await self._promoter.rollback_to_version(
                    skill_name=skill_name,
                    version=version,
                    reason=reason,
                )
            else:
                return False, "no rollback collaborator configured"
        except Exception as exc:  # noqa: BLE001 - policy must not break the caller
            return False, f"{type(exc).__name__}: {exc}"

        return True, None

    @staticmethod
    def _evidence_dict(evidence: PairwiseEvidence) -> dict[str, Any]:
        """JSON-ready view of the two statistical readings."""

        return {
            "control_version": evidence.control_version,
            "treatment_version": evidence.treatment_version,
            "control_rate": evidence.control_rate,
            "treatment_rate": evidence.treatment_rate,
            "delta": evidence.delta,
            "z_score": evidence.z_score,
            "p_superiority": evidence.p_superiority,
            "p_harm": evidence.p_harm,
            "bayes_superiority": evidence.bayes_superiority,
            "bayes_harm": evidence.bayes_harm,
        }
