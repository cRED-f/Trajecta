"""Threshold regression checks between two recorded skill versions.

Distinct from ``skills.evaluation.regression``, which compares two
in-memory metric snapshots (baseline vs candidate during evaluation).
This detector compares *live* execution samples read back from
``skill_execution_metrics``, so it is named ``VersionRegressionDetector``.
"""

from __future__ import annotations

from typing import Any

from server.src.skills.analytics import SkillAnalytics


class VersionRegressionDetector:
    """Flag a current version that is worse than a stable version."""

    # Absolute drop in success rate (0..1) tolerated before flagging.
    SUCCESS_DROP = 0.15
    # Multiplier on average latency before flagging.
    LATENCY_INCREASE = 1.5
    # Multiplier on average tool failures before flagging.
    FAILURE_INCREASE = 2.0

    def __init__(self, analytics: SkillAnalytics) -> None:
        self._analytics = analytics

    async def check(
        self,
        skill_name: str,
        stable_version: str,
        current_version: str,
    ) -> dict[str, Any]:
        """Compare recorded metrics for two versions of the same skill."""

        stable = await self._analytics.summary(skill_name, stable_version)
        current = await self._analytics.summary(skill_name, current_version)

        # An empty side has no signal: AVG() over zero rows would read as a
        # perfect score, which must not be mistaken for a regression (or for
        # a healthy baseline). Refuse to decide without data on both sides.
        if int(stable["total"]) == 0 or int(current["total"]) == 0:
            return {
                "regression": False,
                "reasons": [],
                "insufficient_data": True,
            }

        reasons: list[str] = []

        if (
            float(current["success_rate"])
            < float(stable["success_rate"]) - self.SUCCESS_DROP
        ):
            reasons.append("success_rate_drop")

        if (
            float(current["latency"])
            > float(stable["latency"]) * self.LATENCY_INCREASE
        ):
            reasons.append("latency_regression")

        if (
            float(current["failures"])
            > float(stable["failures"]) * self.FAILURE_INCREASE
        ):
            reasons.append("tool_failures")

        if not reasons:
            return {
                "regression": False,
                "reasons": [],
                "insufficient_data": False,
            }

        return {
            "regression": True,
            "reasons": reasons,
            "severity": "high" if len(reasons) >= 2 else "medium",
            "insufficient_data": False,
        }
