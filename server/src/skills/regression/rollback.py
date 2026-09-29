"""Apply the automatic rollback policy when a version regresses."""

from __future__ import annotations

import uuid

from datetime import UTC, datetime
from typing import Any

from server.src.memory.storage.sqlite import SQLiteDatabase
from server.src.skills.promotion.promoter import SkillPromoter
from server.src.skills.regression.detector import VersionRegressionDetector


def _now() -> str:
    return datetime.now(UTC).isoformat()


class AutomaticRollback:
    """Detect a regression, log it, and restore the stable version."""

    def __init__(
        self,
        detector: VersionRegressionDetector,
        promoter: SkillPromoter,
        db: SQLiteDatabase,
    ) -> None:
        self._detector = detector
        self._promoter = promoter
        self._db = db

    async def evaluate(
        self,
        skill_name: str,
        stable_version: str,
        current_version: str,
    ) -> dict[str, Any]:
        """Run the policy once. Never raises for a policy miss.

        The regression row is written *before* the rollback is attempted, so
        a failed rollback still leaves a durable trace (``rolled_back = 0``).
        """

        result = await self._detector.check(
            skill_name,
            stable_version,
            current_version,
        )

        if not result.get("regression"):
            return result

        reasons: list[str] = list(result.get("reasons", []))
        reason = ", ".join(reasons) or "regression"

        regression_id = await self._log(
            skill_name=skill_name,
            bad_version=current_version,
            stable_version=stable_version,
            reason=reason,
            severity=str(result.get("severity", "medium")),
        )

        try:
            rollback = await self._promoter.rollback_to_version(
                skill_name=skill_name,
                version=stable_version,
                reason=reason,
            )
        except ValueError as exc:
            # The stable version may already be active, or have vanished.
            # The regression is logged; surface why the restore did not run.
            return {
                "rolled_back": False,
                "reason": result,
                "error": str(exc),
                "regression_id": regression_id,
            }

        await self._db.execute(
            "UPDATE skill_regressions SET rolled_back = 1 WHERE id = ?",
            (regression_id,),
        )

        return {
            "rolled_back": True,
            "reason": result,
            "rollback": {
                "skill": rollback["skill"],
                "from": rollback["rolled_back_from"],
                "to": rollback["rolled_back_to"],
            },
            "regression_id": regression_id,
        }

    async def history(self, skill_name: str, limit: int = 50) -> list[dict[str, Any]]:
        """Regression log for one skill, newest first."""

        rows = await self._db.fetch(
            """
            SELECT *
            FROM skill_regressions
            WHERE skill_name = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (skill_name, limit),
        )

        return [
            {
                "id": row["id"],
                "bad_version": row["bad_version"],
                "stable_version": row["stable_version"],
                "reason": row["reason"],
                "severity": row["severity"],
                "rolled_back": bool(row["rolled_back"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    async def _log(
        self,
        *,
        skill_name: str,
        bad_version: str,
        stable_version: str,
        reason: str,
        severity: str,
    ) -> str:
        regression_id = uuid.uuid4().hex

        await self._db.execute(
            """
            INSERT INTO skill_regressions(
                id,
                skill_name,
                bad_version,
                stable_version,
                reason,
                severity,
                rolled_back,
                created_at
            )
            VALUES(?, ?, ?, ?, ?, ?, 0, ?)
            """,
            (
                regression_id,
                skill_name,
                bad_version,
                stable_version,
                reason,
                severity,
                _now(),
            ),
        )

        return regression_id
