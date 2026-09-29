"""Trajectory Store — raw execution data used by learning/evaluation.

This is intentionally separate from semantic/episodic memory. A trajectory is
append-only execution evidence: user goal, model/tool events, final outcome and
run metadata. Candidate-skill mining should consume this store rather than
trusting free-form memory.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from server.src.memory.storage.sqlite import SQLiteDatabase


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


class TrajectoryStore:
    def __init__(self, db: SQLiteDatabase) -> None:
        self._db = db

    async def begin(
        self,
        *,
        goal: str,
        thread_id: str,
        user_id: str = "local",
        metadata: dict[str, Any] | None = None,
    ) -> tuple[str, str]:
        task_id = uuid.uuid4().hex
        trajectory_id = uuid.uuid4().hex
        created = _now()
        await self._db.execute(
            """
            INSERT INTO tasks(id, user_id, thread_id, created_at, status, goal, metadata)
            VALUES (?, ?, ?, ?, 'running', ?, ?)
            """,
            (task_id, user_id, thread_id, created, goal, json.dumps(metadata or {}, ensure_ascii=False)),
        )
        await self._db.execute(
            """
            INSERT INTO trajectories(id, task_id, created_at, steps, outcome, metadata)
            VALUES (?, ?, ?, '[]', NULL, ?)
            """,
            (trajectory_id, task_id, created, json.dumps(metadata or {}, ensure_ascii=False)),
        )
        return task_id, trajectory_id

    async def append(
        self,
        trajectory_id: str,
        *,
        event_type: str,
        data: dict[str, Any] | None = None,
        source: str | None = None,
    ) -> None:
        row = await self._db.fetchone(
            "SELECT steps FROM trajectories WHERE id = ?", (trajectory_id,)
        )
        if row is None:
            raise ValueError(f"Trajectory {trajectory_id!r} not found")
        steps = _loads(row.get("steps"), [])
        if not isinstance(steps, list):
            steps = []
        steps.append(
            {
                "at": _now(),
                "type": event_type,
                "source": source,
                "data": data or {},
            }
        )
        # Keep local trajectory records bounded; huge raw artifacts should stay
        # in their original files/traces and be referenced by path/id.
        if len(steps) > 5_000:
            steps = steps[-5_000:]
        await self._db.execute(
            "UPDATE trajectories SET steps = ? WHERE id = ?",
            (json.dumps(steps, ensure_ascii=False, default=str), trajectory_id),
        )

    async def finish(
        self,
        trajectory_id: str,
        *,
        outcome: str,
        result: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        row = await self._db.fetchone(
            "SELECT task_id, metadata FROM trajectories WHERE id = ?", (trajectory_id,)
        )
        if row is None:
            raise ValueError(f"Trajectory {trajectory_id!r} not found")
        current_meta = _loads(row.get("metadata"), {})
        if not isinstance(current_meta, dict):
            current_meta = {}
        current_meta.update(metadata or {})
        await self._db.execute(
            "UPDATE trajectories SET outcome = ?, metadata = ? WHERE id = ?",
            (outcome, json.dumps(current_meta, ensure_ascii=False, default=str), trajectory_id),
        )
        await self._db.execute(
            "UPDATE tasks SET status = ?, result = ? WHERE id = ?",
            ("complete" if outcome == "success" else outcome, result, row["task_id"]),
        )

    async def get(self, trajectory_id: str) -> dict[str, Any] | None:
        row = await self._db.fetchone("SELECT * FROM trajectories WHERE id = ?", (trajectory_id,))
        if row is None:
            return None
        value = dict(row)
        value["steps"] = _loads(value.get("steps"), [])
        value["metadata"] = _loads(value.get("metadata"), {})
        return value

    async def count(self, *, outcome: str | None = None) -> int:
        if outcome is None:
            row = await self._db.fetchone("SELECT COUNT(*) AS count FROM trajectories")
        else:
            row = await self._db.fetchone(
                """
                SELECT COUNT(*) AS count
                FROM trajectories
                WHERE outcome = ?
                """,
                (outcome,),
            )
        return int((row or {}).get("count") or 0)

    async def count_successful(self) -> int:
        return await self.count(outcome="success")

    async def list(
        self,
        *,
        outcome: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        if outcome:
            rows = await self._db.fetch(
                "SELECT * FROM trajectories WHERE outcome = ? ORDER BY created_at DESC LIMIT ?",
                (outcome, max(1, min(limit, 500))),
            )
        else:
            rows = await self._db.fetch(
                "SELECT * FROM trajectories ORDER BY created_at DESC LIMIT ?",
                (max(1, min(limit, 500)),),
            )
        result: list[dict[str, Any]] = []
        for row in rows:
            value = dict(row)
            value["steps"] = _loads(value.get("steps"), [])
            value["metadata"] = _loads(value.get("metadata"), {})
            result.append(value)
        return result

    async def list_with_tasks(
        self,
        *,
        outcome: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Return trajectories together with their original task goal.

        SkillMiner needs the goal, not only raw event steps.
        """
        limit = max(1, min(limit, 500))

        if outcome:
            rows = await self._db.fetch(
                """
                SELECT tr.*, t.goal AS goal, t.result AS task_result,
                       t.status AS task_status, t.thread_id AS thread_id,
                       t.user_id AS user_id, t.metadata AS task_metadata
                FROM trajectories tr
                JOIN tasks t ON t.id = tr.task_id
                WHERE tr.outcome = ?
                ORDER BY tr.created_at DESC
                LIMIT ?
                """,
                (outcome, limit),
            )
        else:
            rows = await self._db.fetch(
                """
                SELECT tr.*, t.goal AS goal, t.result AS task_result,
                       t.status AS task_status, t.thread_id AS thread_id,
                       t.user_id AS user_id, t.metadata AS task_metadata
                FROM trajectories tr
                JOIN tasks t ON t.id = tr.task_id
                ORDER BY tr.created_at DESC
                LIMIT ?
                """,
                (limit,),
            )

        result: list[dict[str, Any]] = []
        for row in rows:
            value = dict(row)
            value["steps"] = _loads(value.get("steps"), [])
            value["metadata"] = _loads(value.get("metadata"), {})
            value["task_metadata"] = _loads(value.get("task_metadata"), {})
            result.append(value)
        return result

    async def get_with_task(self, trajectory_id: str) -> dict[str, Any] | None:
        rows = await self._db.fetch(
            """
            SELECT tr.*, t.goal AS goal, t.result AS task_result,
                   t.status AS task_status, t.thread_id AS thread_id,
                   t.user_id AS user_id, t.metadata AS task_metadata
            FROM trajectories tr
            JOIN tasks t ON t.id = tr.task_id
            WHERE tr.id = ?
            LIMIT 1
            """,
            (trajectory_id,),
        )
        if not rows:
            return None

        value = dict(rows[0])
        value["steps"] = _loads(value.get("steps"), [])
        value["metadata"] = _loads(value.get("metadata"), {})
        value["task_metadata"] = _loads(value.get("task_metadata"), {})
        return value
