"""Persistent local state used by Trajecta's personal-agent tools."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from server.src.memory.storage.sqlite import SQLiteDatabase


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso_now() -> str:
    return utc_now().isoformat()


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def json_loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


class PersonalAgentStore:
    """SQLite-backed tasks, schedules, notifications, candidates and sessions."""

    def __init__(self, db: SQLiteDatabase) -> None:
        self._db = db

    # ------------------------------------------------------------------
    # Tasks
    # ------------------------------------------------------------------

    async def create_task(
        self,
        *,
        title: str,
        notes: str = "",
        due_at: str | None = None,
        priority: str = "normal",
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        task_id = uuid.uuid4().hex
        now = iso_now()
        await self._db.execute(
            """
            INSERT INTO personal_tasks(
                id, title, notes, status, priority, due_at, tags, created_at, updated_at
            ) VALUES (?, ?, ?, 'open', ?, ?, ?, ?, ?)
            """,
            (task_id, title.strip(), notes.strip(), priority, due_at, json_dumps(tags or []), now, now),
        )
        return await self.get_task(task_id) or {}

    async def get_task(self, task_id: str) -> dict[str, Any] | None:
        row = await self._db.fetchone("SELECT * FROM personal_tasks WHERE id = ?", (task_id,))
        return self._decode_task(row) if row else None

    async def list_tasks(
        self,
        *,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        if status:
            rows = await self._db.fetch(
                """
                SELECT * FROM personal_tasks
                WHERE status = ?
                ORDER BY CASE WHEN due_at IS NULL THEN 1 ELSE 0 END, due_at, created_at DESC
                LIMIT ?
                """,
                (status, max(1, min(limit, 200))),
            )
        else:
            rows = await self._db.fetch(
                """
                SELECT * FROM personal_tasks
                ORDER BY CASE WHEN status = 'open' THEN 0 ELSE 1 END,
                         CASE WHEN due_at IS NULL THEN 1 ELSE 0 END,
                         due_at, created_at DESC
                LIMIT ?
                """,
                (max(1, min(limit, 200)),),
            )
        return [self._decode_task(row) for row in rows]

    async def update_task(
        self,
        task_id: str,
        *,
        title: str | None = None,
        notes: str | None = None,
        status: str | None = None,
        priority: str | None = None,
        due_at: str | None = None,
        clear_due_at: bool = False,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        current = await self.get_task(task_id)
        if current is None:
            raise ValueError(f"Task {task_id!r} not found")

        fields: list[str] = ["updated_at = ?"]
        params: list[Any] = [iso_now()]
        for column, value in (
            ("title", title),
            ("notes", notes),
            ("status", status),
            ("priority", priority),
        ):
            if value is not None:
                fields.append(f"{column} = ?")
                params.append(value)
        if due_at is not None or clear_due_at:
            fields.append("due_at = ?")
            params.append(due_at if not clear_due_at else None)
        if tags is not None:
            fields.append("tags = ?")
            params.append(json_dumps(tags))
        params.append(task_id)
        await self._db.execute(
            f"UPDATE personal_tasks SET {', '.join(fields)} WHERE id = ?",
            tuple(params),
        )
        return await self.get_task(task_id) or {}

    async def delete_task(self, task_id: str) -> bool:
        current = await self.get_task(task_id)
        if current is None:
            return False
        await self._db.execute("DELETE FROM personal_tasks WHERE id = ?", (task_id,))
        return True

    @staticmethod
    def _decode_task(row: dict[str, Any]) -> dict[str, Any]:
        value = dict(row)
        value["tags"] = json_loads(value.get("tags"), [])
        return value

    # ------------------------------------------------------------------
    # Scheduled jobs
    # ------------------------------------------------------------------

    @staticmethod
    def _next_run(
        schedule_type: str,
        schedule_expr: str,
        timezone: str,
        *,
        after: datetime | None = None,
    ) -> str | None:
        now_utc = after or utc_now()
        tz = ZoneInfo(timezone)
        local_now = now_utc.astimezone(tz)

        if schedule_type == "once":
            target = datetime.fromisoformat(schedule_expr)
            if target.tzinfo is None:
                target = target.replace(tzinfo=tz)
            target_utc = target.astimezone(UTC)
            return target_utc.isoformat() if target_utc > now_utc else None

        if schedule_type == "interval":
            seconds = int(schedule_expr)
            if seconds < 60:
                raise ValueError("interval schedules must be at least 60 seconds")
            return (now_utc + timedelta(seconds=seconds)).isoformat()

        if schedule_type == "cron":
            try:
                from croniter import croniter
            except ImportError as exc:
                raise RuntimeError("croniter is required for cron schedules") from exc
            next_local = croniter(schedule_expr, local_now).get_next(datetime)
            if next_local.tzinfo is None:
                next_local = next_local.replace(tzinfo=tz)
            return next_local.astimezone(UTC).isoformat()

        raise ValueError("schedule_type must be one of: once, interval, cron")

    async def create_schedule(
        self,
        *,
        name: str,
        prompt: str,
        schedule_type: str,
        schedule_expr: str,
        timezone: str = "UTC",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        job_id = uuid.uuid4().hex
        now = iso_now()
        next_run_at = self._next_run(schedule_type, schedule_expr, timezone)
        await self._db.execute(
            """
            INSERT INTO scheduled_jobs(
                id, name, prompt, schedule_type, schedule_expr, timezone,
                next_run_at, enabled, created_at, updated_at, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
            """,
            (
                job_id,
                name.strip(),
                prompt.strip(),
                schedule_type,
                schedule_expr,
                timezone,
                next_run_at,
                now,
                now,
                json_dumps(metadata or {}),
            ),
        )
        return await self.get_schedule(job_id) or {}

    async def get_schedule(self, job_id: str) -> dict[str, Any] | None:
        row = await self._db.fetchone("SELECT * FROM scheduled_jobs WHERE id = ?", (job_id,))
        return self._decode_schedule(row) if row else None

    async def list_schedules(self, *, enabled_only: bool = False) -> list[dict[str, Any]]:
        query = "SELECT * FROM scheduled_jobs"
        params: tuple[Any, ...] = ()
        if enabled_only:
            query += " WHERE enabled = 1"
        query += " ORDER BY next_run_at IS NULL, next_run_at, created_at DESC"
        rows = await self._db.fetch(query, params)
        return [self._decode_schedule(row) for row in rows]

    async def update_schedule(
        self,
        job_id: str,
        *,
        name: str | None = None,
        prompt: str | None = None,
        schedule_type: str | None = None,
        schedule_expr: str | None = None,
        timezone: str | None = None,
        enabled: bool | None = None,
    ) -> dict[str, Any]:
        current = await self.get_schedule(job_id)
        if current is None:
            raise ValueError(f"Schedule {job_id!r} not found")

        new_type = schedule_type or current["schedule_type"]
        new_expr = schedule_expr or current["schedule_expr"]
        new_tz = timezone or current["timezone"]
        new_enabled = bool(current["enabled"]) if enabled is None else enabled
        next_run = self._next_run(new_type, new_expr, new_tz) if new_enabled else None

        await self._db.execute(
            """
            UPDATE scheduled_jobs
            SET name = ?, prompt = ?, schedule_type = ?, schedule_expr = ?, timezone = ?,
                enabled = ?, next_run_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                name if name is not None else current["name"],
                prompt if prompt is not None else current["prompt"],
                new_type,
                new_expr,
                new_tz,
                1 if new_enabled else 0,
                next_run,
                iso_now(),
                job_id,
            ),
        )
        return await self.get_schedule(job_id) or {}

    async def delete_schedule(self, job_id: str) -> bool:
        current = await self.get_schedule(job_id)
        if current is None:
            return False
        await self._db.execute("DELETE FROM scheduled_jobs WHERE id = ?", (job_id,))
        return True

    async def due_schedules(self, *, now: datetime | None = None, limit: int = 20) -> list[dict[str, Any]]:
        at = (now or utc_now()).isoformat()
        rows = await self._db.fetch(
            """
            SELECT * FROM scheduled_jobs
            WHERE enabled = 1 AND next_run_at IS NOT NULL AND next_run_at <= ?
            ORDER BY next_run_at
            LIMIT ?
            """,
            (at, max(1, min(limit, 100))),
        )
        return [self._decode_schedule(row) for row in rows]

    async def mark_schedule_ran(self, job_id: str, *, result: str) -> dict[str, Any]:
        current = await self.get_schedule(job_id)
        if current is None:
            raise ValueError(f"Schedule {job_id!r} not found")
        now = utc_now()
        if current["schedule_type"] == "once":
            next_run = None
            enabled = 0
        else:
            next_run = self._next_run(
                current["schedule_type"],
                current["schedule_expr"],
                current["timezone"],
                after=now,
            )
            enabled = 1
        await self._db.execute(
            """
            UPDATE scheduled_jobs
            SET last_run_at = ?, last_result = ?, next_run_at = ?, enabled = ?, updated_at = ?
            WHERE id = ?
            """,
            (now.isoformat(), result[:100_000], next_run, enabled, now.isoformat(), job_id),
        )
        return await self.get_schedule(job_id) or {}

    async def bind_schedule_conversation(self, job_id: str, conversation_id: str) -> None:
        await self._db.execute(
            "UPDATE scheduled_jobs SET conversation_id = ?, updated_at = ? WHERE id = ?",
            (conversation_id, iso_now(), job_id),
        )

    @staticmethod
    def _decode_schedule(row: dict[str, Any]) -> dict[str, Any]:
        value = dict(row)
        value["enabled"] = bool(value.get("enabled"))
        value["metadata"] = json_loads(value.get("metadata"), {})
        return value

    # ------------------------------------------------------------------
    # Notifications
    # ------------------------------------------------------------------

    async def create_notification(
        self,
        *,
        title: str,
        body: str,
        level: str = "info",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        notification_id = uuid.uuid4().hex
        await self._db.execute(
            """
            INSERT INTO notifications(id, title, body, level, created_at, metadata)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (notification_id, title, body, level, iso_now(), json_dumps(metadata or {})),
        )
        row = await self._db.fetchone("SELECT * FROM notifications WHERE id = ?", (notification_id,))
        return self._decode_notification(row or {})

    async def list_notifications(self, *, unread_only: bool = False, limit: int = 50) -> list[dict[str, Any]]:
        if unread_only:
            rows = await self._db.fetch(
                "SELECT * FROM notifications WHERE read_at IS NULL ORDER BY created_at DESC LIMIT ?",
                (max(1, min(limit, 200)),),
            )
        else:
            rows = await self._db.fetch(
                "SELECT * FROM notifications ORDER BY created_at DESC LIMIT ?",
                (max(1, min(limit, 200)),),
            )
        return [self._decode_notification(row) for row in rows]

    async def mark_notification_read(self, notification_id: str) -> bool:
        row = await self._db.fetchone("SELECT id FROM notifications WHERE id = ?", (notification_id,))
        if row is None:
            return False
        await self._db.execute(
            "UPDATE notifications SET read_at = ? WHERE id = ?", (iso_now(), notification_id)
        )
        return True

    @staticmethod
    def _decode_notification(row: dict[str, Any]) -> dict[str, Any]:
        value = dict(row)
        value["metadata"] = json_loads(value.get("metadata"), {})
        return value

    # ------------------------------------------------------------------
    # Session history
    # ------------------------------------------------------------------

    async def search_sessions(self, query: str, *, limit: int = 20) -> list[dict[str, Any]]:
        pattern = f"%{query.strip()}%"
        rows = await self._db.fetch(
            """
            SELECT
                c.id AS conversation_id,
                c.title,
                c.updated_at,
                m.id AS message_id,
                m.role,
                m.content,
                m.created_at
            FROM chat_messages m
            JOIN conversations c ON c.id = m.conversation_id
            WHERE m.content LIKE ? OR COALESCE(c.title, '') LIKE ?
            ORDER BY m.created_at DESC
            LIMIT ?
            """,
            (pattern, pattern, max(1, min(limit, 100))),
        )
        for row in rows:
            row["content"] = str(row.get("content", ""))[:4_000]
        return rows

    async def read_session(self, conversation_id: str, *, limit: int = 100) -> dict[str, Any] | None:
        conversation = await self._db.fetchone(
            "SELECT * FROM conversations WHERE id = ?", (conversation_id,)
        )
        if conversation is None:
            return None
        messages = await self._db.fetch(
            """
            SELECT id, role, content, created_at, status
            FROM chat_messages
            WHERE conversation_id = ?
            ORDER BY created_at ASC, rowid ASC
            LIMIT ?
            """,
            (conversation_id, max(1, min(limit, 500))),
        )
        return {"conversation": conversation, "messages": messages}

    # ------------------------------------------------------------------
    # Skill candidates (not promoted skills)
    # ------------------------------------------------------------------

    async def create_skill_candidate(
        self,
        *,
        name: str,
        description: str,
        content: str,
        source_trajectory_ids: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        candidate_id = uuid.uuid4().hex
        now = iso_now()
        await self._db.execute(
            """
            INSERT INTO skill_candidates(
                id, name, description, content, status, source_trajectory_ids,
                created_at, updated_at, metadata
            ) VALUES (?, ?, ?, ?, 'candidate', ?, ?, ?, ?)
            """,
            (
                candidate_id,
                name,
                description,
                content,
                json_dumps(source_trajectory_ids or []),
                now,
                now,
                json_dumps(metadata or {}),
            ),
        )
        return await self.get_skill_candidate(candidate_id) or {}

    async def get_skill_candidate(self, candidate_id: str) -> dict[str, Any] | None:
        row = await self._db.fetchone(
            "SELECT * FROM skill_candidates WHERE id = ?", (candidate_id,)
        )
        return self._decode_candidate(row) if row else None

    async def list_skill_candidates(self, *, status: str | None = None) -> list[dict[str, Any]]:
        if status:
            rows = await self._db.fetch(
                "SELECT * FROM skill_candidates WHERE status = ? ORDER BY updated_at DESC",
                (status,),
            )
        else:
            rows = await self._db.fetch(
                "SELECT * FROM skill_candidates ORDER BY updated_at DESC"
            )
        return [self._decode_candidate(row) for row in rows]

    async def update_skill_candidate(
        self,
        candidate_id: str,
        *,
        description: str | None = None,
        content: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        current = await self.get_skill_candidate(candidate_id)
        if current is None:
            raise ValueError(f"Skill candidate {candidate_id!r} not found")
        await self._db.execute(
            """
            UPDATE skill_candidates
            SET description = ?, content = ?, status = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                description if description is not None else current["description"],
                content if content is not None else current["content"],
                status if status is not None else current["status"],
                iso_now(),
                candidate_id,
            ),
        )
        return await self.get_skill_candidate(candidate_id) or {}

    @staticmethod
    def _decode_candidate(row: dict[str, Any]) -> dict[str, Any]:
        value = dict(row)
        value["source_trajectory_ids"] = json_loads(value.get("source_trajectory_ids"), [])
        value["metadata"] = json_loads(value.get("metadata"), {})
        return value

    # ------------------------------------------------------------------
    # Trajectory lookup (capture/evaluation comes in Trajecta's learning slice)
    # ------------------------------------------------------------------

    async def search_trajectories(self, *, outcome: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        if outcome:
            rows = await self._db.fetch(
                "SELECT * FROM trajectories WHERE outcome = ? ORDER BY created_at DESC LIMIT ?",
                (outcome, max(1, min(limit, 100))),
            )
        else:
            rows = await self._db.fetch(
                "SELECT * FROM trajectories ORDER BY created_at DESC LIMIT ?",
                (max(1, min(limit, 100)),),
            )
        for row in rows:
            row["steps"] = json_loads(row.get("steps"), [])
            row["metadata"] = json_loads(row.get("metadata"), {})
        return rows
