"""Aggregate execution metrics for a skill (optionally one version)."""

from __future__ import annotations

from typing import Any

from server.src.memory.storage.sqlite import SQLiteDatabase

# SQLite's AVG() is NULL over an empty set. Downstream comparisons do
# arithmetic on these fields, so every value is normalized to a number.
_METRIC_KEYS = ("total", "success_rate", "latency", "tokens", "failures")


def _number(value: Any) -> float | int:
    if value is None:
        return 0
    if isinstance(value, int):
        return value
    return float(value)


class SkillAnalytics:
    def __init__(self, db: SQLiteDatabase) -> None:
        self._db = db

    async def summary(
        self,
        skill_name: str,
        version: str | None = None,
    ) -> dict[str, Any]:
        """Return aggregate metrics, always with numeric fields.

        ``success_rate`` is 0..1 (``AVG`` of the 0/1 ``success`` column).
        Returns zeros rather than an empty dict when a skill or version has
        no recorded executions, so callers never branch on missing keys.
        """

        query = """
            SELECT
                COUNT(*) AS total,
                AVG(success) AS success_rate,
                AVG(latency_ms) AS latency,
                AVG(tokens_used) AS tokens,
                AVG(tool_failures) AS failures
            FROM skill_execution_metrics
            WHERE skill_name = ?
        """

        params: list[Any] = [skill_name]

        if version:
            query += " AND skill_version = ?"
            params.append(version)

        row = await self._db.fetchone(query, tuple(params))

        summary: dict[str, Any] = {
            key: _number((row or {}).get(key)) for key in _METRIC_KEYS
        }

        # Latest sample per version, so a dashboard can show coverage.
        samples = await self._db.fetch(
            """
            SELECT skill_version, COUNT(*) AS executions
            FROM skill_execution_metrics
            WHERE skill_name = ?
            GROUP BY skill_version
            ORDER BY skill_version
            """,
            (skill_name,),
        )

        summary["skill"] = skill_name
        summary["versions"] = [
            {
                "version": item["skill_version"],
                "executions": int(item["executions"] or 0),
            }
            for item in samples
        ]

        return summary
