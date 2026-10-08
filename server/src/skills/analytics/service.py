"""Read-side analytics over ``skill_execution_metrics``.

The table is shared with the live attribution path, so experiments and the
regression monitor always see the same rows the dashboard does. A row
becomes "completed" when ``completed_at`` is set, which keeps an
in-flight run from being counted as a failure before it finishes.
"""

from __future__ import annotations

import json
import uuid

from datetime import UTC, datetime
from typing import Any

from server.src.memory.storage.sqlite import SQLiteDatabase


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _loads(value: Any, default: Any) -> Any:
    if not isinstance(value, str) or not value:
        return default

    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


class SkillAnalyticsService:
    def __init__(self, db: SQLiteDatabase) -> None:
        self._db = db

    async def bind_run(
        self,
        *,
        trajectory_id: str,
        skill_name: str,
        skill_version: str,
        experiment_id: str | None,
        arm_kind: str,
        unit_id: str | None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Reserve a metrics row for a skill before its run has an outcome.

        Idempotent on ``(trajectory_id, skill_name)``: a run that is bound
        twice — or already attributed — keeps the row it has.
        """

        metric_id = uuid.uuid4().hex

        await self._db.execute(
            """
            INSERT OR IGNORE INTO skill_execution_metrics(
                id,
                trajectory_id,
                experiment_id,
                skill_name,
                skill_version,
                arm_kind,
                unit_id,
                metadata,
                created_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                metric_id,
                trajectory_id,
                experiment_id,
                skill_name,
                skill_version,
                arm_kind,
                unit_id,
                json.dumps(metadata or {}, ensure_ascii=False),
                _now(),
            ),
        )

        row = await self._db.fetchone(
            """
            SELECT id FROM skill_execution_metrics
            WHERE trajectory_id = ? AND skill_name = ?
            """,
            (trajectory_id, skill_name),
        )

        return str((row or {}).get("id") or metric_id)

    async def complete_trajectory(
        self,
        trajectory_id: str,
        *,
        success: bool | None,
        duration_seconds: float = 0.0,
        input_tokens: int = 0,
        output_tokens: int = 0,
        tool_calls: int = 0,
        tool_errors: int = 0,
        metadata: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Close out every row a run reserved, with its runtime numbers."""

        rows = await self._db.fetch(
            "SELECT * FROM skill_execution_metrics WHERE trajectory_id = ?",
            (trajectory_id,),
        )

        for row in rows:
            merged = _loads(row.get("metadata"), {})

            if not isinstance(merged, dict):
                merged = {}

            merged.update(metadata or {})

            await self._db.execute(
                """
                UPDATE skill_execution_metrics
                SET success = ?,
                    outcome_verified = ?,
                    duration_seconds = ?,
                    latency_ms = CASE WHEN ? > 0 THEN ? * 1000 ELSE latency_ms END,
                    tokens_used = CASE WHEN ? > 0 THEN ? ELSE tokens_used END,
                    tool_failures = CASE WHEN ? > 0 THEN ? ELSE tool_failures END,
                    input_tokens = ?,
                    output_tokens = ?,
                    tool_calls = ?,
                    tool_errors = ?,
                    completed_at = ?,
                    metadata = ?
                WHERE id = ?
                """,
                (
                    int(success) if success is not None else 0,
                    int(success is not None),
                    float(duration_seconds),
                    float(duration_seconds), float(duration_seconds),
                    max(0, int(input_tokens)) + max(0, int(output_tokens)),
                    max(0, int(input_tokens)) + max(0, int(output_tokens)),
                    max(0, int(tool_errors)), max(0, int(tool_errors)),
                    max(0, int(input_tokens)),
                    max(0, int(output_tokens)),
                    max(0, int(tool_calls)),
                    max(0, int(tool_errors)),
                    _now(),
                    json.dumps(merged, ensure_ascii=False),
                    row["id"],
                ),
            )

        return await self._db.fetch(
            "SELECT * FROM skill_execution_metrics WHERE trajectory_id = ?",
            (trajectory_id,),
        )

    async def version_summary(
        self,
        skill_name: str,
        version: str,
        *,
        limit: int | None = None,
        experiment_id: str | None = None,
    ) -> dict[str, Any]:
        """Aggregate one version's completed runs, optionally one experiment's."""

        clauses = [
            "skill_name = ?",
            "skill_version = ?",
            "completed_at IS NOT NULL",
            "outcome_verified = 1",
        ]

        params: list[Any] = [skill_name, version]

        if experiment_id is not None:
            clauses.append("experiment_id = ?")
            params.append(experiment_id)

        where = " AND ".join(clauses)

        if limit:
            rows = await self._db.fetch(
                f"""
                SELECT * FROM skill_execution_metrics
                WHERE {where}
                ORDER BY completed_at DESC
                LIMIT ?
                """,
                (*params, max(1, int(limit))),
            )
        else:
            rows = await self._db.fetch(
                f"SELECT * FROM skill_execution_metrics WHERE {where}",  # noqa: S608
                tuple(params),
            )

        total = len(rows)
        successes = sum(1 for row in rows if int(row.get("success") or 0) == 1)

        tool_call_total = sum(int(row.get("tool_calls") or 0) for row in rows)
        tool_error_total = sum(int(row.get("tool_errors") or 0) for row in rows)

        return {
            "skill_name": skill_name,
            "version": version,
            "total": total,
            "successes": successes,
            "failures": max(0, total - successes),
            "success_rate": (successes / total) if total else 0.0,
            "average_duration_seconds": self._avg(rows, "duration_seconds"),
            "average_input_tokens": self._avg(rows, "input_tokens"),
            "average_output_tokens": self._avg(rows, "output_tokens"),
            "average_total_tokens": self._avg_total_tokens(rows),
            "average_tool_calls": self._avg(rows, "tool_calls"),
            "average_tool_errors": self._avg(rows, "tool_errors"),
            "tool_error_rate": (
                tool_error_total / max(1, tool_call_total) if rows else 0.0
            ),
        }

    async def experiment_arms(self, experiment_id: str) -> list[dict[str, Any]]:
        arms = await self._db.fetch(
            """
            SELECT * FROM skill_experiment_arms
            WHERE experiment_id = ?
            ORDER BY is_control DESC, created_at ASC
            """,
            (experiment_id,),
        )

        experiment = await self._db.fetchone(
            "SELECT skill_name FROM skill_experiments WHERE id = ?",
            (experiment_id,),
        )

        if experiment is None:
            return []

        result: list[dict[str, Any]] = []

        for arm in arms:
            summary = await self.version_summary(
                str(experiment["skill_name"]),
                str(arm["version"]),
                experiment_id=experiment_id,
            )

            value = dict(arm)
            value["metadata"] = _loads(value.get("metadata"), {})
            value["summary"] = summary
            result.append(value)

        return result

    async def skill_dashboard(self, skill_name: str) -> dict[str, Any]:
        """Versions, experiments and regressions for one skill, with metrics."""

        versions = await self._db.fetch(
            """
            SELECT version, status, created_at, metadata
            FROM skill_versions
            WHERE skill_name = ?
            ORDER BY created_at DESC
            """,
            (skill_name,),
        )

        version_rows: list[dict[str, Any]] = []

        for row in versions:
            value = dict(row)
            value["metadata"] = _loads(value.get("metadata"), {})
            value["metrics"] = await self.version_summary(
                skill_name, str(row["version"])
            )
            version_rows.append(value)

        experiments = await self._db.fetch(
            """
            SELECT * FROM skill_experiments
            WHERE skill_name = ?
            ORDER BY created_at DESC
            LIMIT 50
            """,
            (skill_name,),
        )

        for experiment in experiments:
            experiment["metadata"] = _loads(experiment.get("metadata"), {})
            experiment["arms"] = await self.experiment_arms(str(experiment["id"]))

        regressions = await self._db.fetch(
            """
            SELECT * FROM skill_regressions
            WHERE skill_name = ?
            ORDER BY created_at DESC
            LIMIT 50
            """,
            (skill_name,),
        )

        for row in regressions:
            # v13 stored a single free-text reason; v14 adds the structured
            # list alongside it, so an old row still renders as one reason.
            row["reasons"] = _loads(row.get("reasons"), None) or (
                [row["reason"]] if row.get("reason") else []
            )
            row["evidence"] = _loads(row.get("evidence"), {})
            row["rolled_back"] = bool(row.get("rolled_back"))

        return {
            "skill_name": skill_name,
            "versions": version_rows,
            "experiments": experiments,
            "regressions": regressions,
        }

    @staticmethod
    def _avg(rows: list[dict[str, Any]], key: str) -> float:
        if not rows:
            return 0.0

        return sum(float(row.get(key) or 0.0) for row in rows) / len(rows)

    @staticmethod
    def _avg_total_tokens(rows: list[dict[str, Any]]) -> float:
        if not rows:
            return 0.0

        return sum(
            int(row.get("input_tokens") or 0) + int(row.get("output_tokens") or 0)
            for row in rows
        ) / len(rows)
