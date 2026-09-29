"""Per-execution skill performance samples."""

from __future__ import annotations

import uuid

from datetime import UTC, datetime

from server.src.memory.storage.sqlite import SQLiteDatabase


def _now() -> str:
    return datetime.now(UTC).isoformat()


class SkillMetricsCollector:
    """Append one performance sample for a skill execution.

    Rows are append-only: every consumer (analytics, the regression
    detector, A/B comparison) reads them back grouped by
    ``(skill_name, skill_version)``.
    """

    def __init__(self, db: SQLiteDatabase) -> None:
        self._db = db

    async def record(
        self,
        *,
        skill_name: str,
        skill_version: str,
        trajectory_id: str | None,
        success: bool,
        latency_ms: float,
        tokens: int,
        tool_failures: int,
    ) -> str:
        """Store a single execution sample and return its row id."""

        metric_id = uuid.uuid4().hex

        await self._db.execute(
            """
            INSERT INTO skill_execution_metrics(
                id,
                skill_name,
                skill_version,
                trajectory_id,
                success,
                latency_ms,
                tokens_used,
                tool_failures,
                created_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                metric_id,
                skill_name,
                skill_version,
                trajectory_id,
                int(success),
                latency_ms,
                tokens,
                tool_failures,
                _now(),
            ),
        )

        return metric_id
