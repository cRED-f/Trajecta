"""A/B experiment routing: split traffic between two skill versions."""

from __future__ import annotations

import random
import uuid

from datetime import UTC, datetime
from typing import Any

from server.src.memory.storage.sqlite import SQLiteDatabase


def _now() -> str:
    return datetime.now(UTC).isoformat()


class SkillExperimentRouter:
    """Decide which version of a skill should serve a given execution."""

    def __init__(self, db: SQLiteDatabase) -> None:
        self._db = db

    async def choose_version(self, skill_name: str) -> str | None:
        """Return the version to run, or None when no experiment applies.

        Traffic lands on the experiment arm with probability
        ``traffic_percent / 100``; the rest stays on control.
        """

        experiment = await self._db.fetchone(
            """
            SELECT *
            FROM skill_experiments
            WHERE skill_name = ? AND status = 'running'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (skill_name,),
        )

        if experiment is None:
            return None

        percentage = int(experiment["traffic_percent"])

        if random.randint(1, 100) <= percentage:
            return str(experiment["experiment_version"])

        return str(experiment["control_version"])

    async def create(
        self,
        *,
        skill_name: str,
        control_version: str,
        experiment_version: str,
        traffic_percent: int,
    ) -> dict[str, Any]:
        """Open an experiment, closing any experiment already running.

        Only one experiment may run per skill: ``choose_version`` reads the
        running row, and two of them would make the split ambiguous.
        """

        if not 1 <= traffic_percent <= 100:
            raise ValueError("traffic_percent must be between 1 and 100")

        if control_version == experiment_version:
            raise ValueError("control and experiment versions must differ")

        await self._db.execute(
            """
            UPDATE skill_experiments
            SET status = 'superseded'
            WHERE skill_name = ? AND status = 'running'
            """,
            (skill_name,),
        )

        experiment_id = uuid.uuid4().hex

        await self._db.execute(
            """
            INSERT INTO skill_experiments(
                id,
                skill_name,
                control_version,
                experiment_version,
                traffic_percent,
                status,
                created_at
            )
            VALUES(?, ?, ?, ?, ?, 'running', ?)
            """,
            (
                experiment_id,
                skill_name,
                control_version,
                experiment_version,
                traffic_percent,
                _now(),
            ),
        )

        return {
            "id": experiment_id,
            "skill_name": skill_name,
            "control_version": control_version,
            "experiment_version": experiment_version,
            "traffic_percent": traffic_percent,
            "status": "running",
        }

    async def list(self, skill_name: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch(
            """
            SELECT *
            FROM skill_experiments
            WHERE skill_name = ?
            ORDER BY created_at DESC
            """,
            (skill_name,),
        )

        return [
            {
                "id": row["id"],
                "skill_name": row["skill_name"],
                "control_version": row["control_version"],
                "experiment_version": row["experiment_version"],
                "traffic_percent": row["traffic_percent"],
                "status": row["status"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    async def stop(self, skill_name: str) -> int:
        """Stop every running experiment for a skill. Returns rows changed."""

        cursor = await self._db.execute(
            """
            UPDATE skill_experiments
            SET status = 'stopped'
            WHERE skill_name = ? AND status = 'running'
            """,
            (skill_name,),
        )

        return int(cursor.rowcount or 0)
