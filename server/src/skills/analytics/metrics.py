"""Per-execution skill performance samples."""

from __future__ import annotations

import json
import uuid

from datetime import UTC, datetime
from typing import Any

from server.src.memory.storage.sqlite import SQLiteDatabase


def _now() -> str:
    return datetime.now(UTC).isoformat()


class SkillMetricsCollector:
    """Append one performance sample for a skill execution.

    Rows are append-only: every consumer (analytics, the regression
    detector, A/B comparison) reads them back grouped by
    ``(skill_name, skill_version)``.

    The runtime columns are optional so offline callers (fixture replay,
    seeded tests) can record a sample without a live run behind it; the
    live attribution path fills them in from ``run.finished``.
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
        experiment_id: str | None = None,
        arm_kind: str = "active",
        unit_id: str | None = None,
        duration_seconds: float | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        tool_calls: int = 0,
        tool_errors: int = 0,
        metadata: dict[str, Any] | None = None,
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
                experiment_id,
                arm_kind,
                unit_id,
                duration_seconds,
                input_tokens,
                output_tokens,
                tool_calls,
                tool_errors,
                completed_at,
                metadata,
                created_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                experiment_id,
                arm_kind,
                unit_id,
                duration_seconds,
                max(0, int(input_tokens)),
                max(0, int(output_tokens)),
                max(0, int(tool_calls)),
                max(0, int(tool_errors)),
                _now(),
                json.dumps(metadata or {}, ensure_ascii=False),
                _now(),
            ),
        )

        return metric_id
