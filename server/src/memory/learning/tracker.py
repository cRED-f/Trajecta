"""Persistent logical tasks spanning chat turns and conversations.

No model call, vector search, or background reflection on the foreground path.
Conservative lexical matching avoids silently merging unrelated projects.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from server.src.memory.storage.sqlite import SQLiteDatabase


def now() -> str:
    return datetime.now(UTC).isoformat()


STOP = frozenset("a an and are as at be by can could do for from how i in is it me my of on or please the this to we what with you your now then next still".split())
CONTINUE = re.compile(r"^(?:please\s+)?(?:continue|resume|keep working|fix it|try again|same task|also|now\s+(?:fix|add|change|update))\b", re.I)
NEW_TOPIC = re.compile(r"^(?:new task|unrelated|different topic|another question)\b", re.I)


def terms(text: str) -> set[str]:
    return {word for word in re.findall(r"[a-z0-9]{3,}", text.casefold()) if word not in STOP}


def similarity(a: str, b: str) -> float:
    left, right = terms(a), terms(b)
    return len(left & right) / max(1, len(left | right))


class TaskTracker:
    def __init__(self, db: SQLiteDatabase) -> None:
        self.db = db

    async def resolve(self, *, goal: str, thread_id: str, user_id: str,
                      metadata: dict[str, Any]) -> str:
        """Fast deterministic best-effort match; uncertain messages get new IDs.

        A human-in-the-loop resume uses its original user message ID. Explicit
        continuation or strong shared terms permit a cross-session match, but
        only within the same user and workspace scope.
        """
        from server.src.memory.episodic.store import EpisodicMemory
        scope = EpisodicMemory.workspace_scope(metadata.get("workspace_path"))
        message_id = metadata.get("user_message_id")
        if metadata.get("approval_resume") and message_id:
            existing = await self.db.fetchone(
                """SELECT tr.task_id AS id FROM trajectories tr JOIN tasks t ON t.id=tr.task_id
                   WHERE t.user_id=? AND t.scope=? AND json_valid(tr.metadata)
                     AND json_extract(tr.metadata,'$.user_message_id')=?
                   ORDER BY tr.created_at DESC LIMIT 1""",
                (user_id, scope, str(message_id)),
            )
            if existing:
                return str(existing["id"])
        if not NEW_TOPIC.match(goal.lstrip()):
            # Within-thread matching is conservative, and only recent tasks
            # can be continued without a strong, explicit reference.
            cutoff = (datetime.now(UTC)-timedelta(days=14)).isoformat()
            rows = await self.db.fetch(
                """SELECT id, goal, thread_id, status, updated_at FROM tasks
                   WHERE user_id=? AND scope=? AND updated_at>=?
                   ORDER BY updated_at DESC LIMIT 25""",
                (user_id, scope, cutoff),
            )
            explicit = bool(CONTINUE.match(goal.lstrip()))
            scored: list[tuple[float, dict[str, Any]]] = []
            for row in rows:
                if row["status"] == "abandoned":
                    continue
                score = similarity(goal, str(row.get("goal") or ""))
                same_thread = row["thread_id"] == thread_id
                # Anaphoric follow-ups ("fix it") only resolve in their own thread.
                if same_thread and (score >= .34 or explicit):
                    scored.append((score + .5, row))
                elif (explicit and score >= .23) or score >= .72:
                    scored.append((score, row))
            scored.sort(key=lambda item: item[0], reverse=True)
            if scored and (len(scored) == 1 or scored[0][0] - scored[1][0] >= .15):
                task_id = str(scored[0][1]["id"])
                await self.db.execute(
                    "UPDATE tasks SET status='active',updated_at=? WHERE id=?",
                    (now(),task_id),
                )
                return task_id
        task_id = uuid.uuid4().hex
        current = now()
        await self.db.execute(
            """INSERT INTO tasks(id,user_id,thread_id,created_at,updated_at,status,
                       goal,metadata,scope,checkpoint_seq)
               VALUES (?,?,?,?,?,'active',?,?,?,0)""",
            (task_id,user_id,thread_id,current,current,goal[:4000],
             json.dumps(metadata,ensure_ascii=False,default=str),scope),
        )
        return task_id

    async def finish_run(self, task_id: str, *, outcome: str, result: str | None) -> None:
        # Interrupted means the task is still active. A final text response is
        # only 'likely_complete', not proof the user's objective was met.
        status = ('active' if outcome == 'interrupted' else
                  'paused' if outcome in {'cancelled','failure'} else 'likely_complete')
        await self.db.execute(
            "UPDATE tasks SET status=?,result=?,updated_at=? WHERE id=?",
            (status, result[:4000] if result else None, now(), task_id),
        )

    async def pause_idle(self, *, minutes: int = 25) -> int:
        cutoff = (datetime.now(UTC)-timedelta(minutes=minutes)).isoformat()
        cur = await self.db.execute(
            "UPDATE tasks SET status='paused' WHERE status IN ('active','likely_complete') AND updated_at<?",
            (cutoff,),
        )
        return int(cur.rowcount)

    async def list(self, *, user_id: str='local', scope: str='local', limit: int=50) -> list[dict[str, Any]]:
        return await self.db.fetch(
            "SELECT id,goal,status,thread_id,created_at,updated_at,scope FROM tasks WHERE user_id=? AND scope=? ORDER BY updated_at DESC LIMIT ?",
            (user_id,scope,max(1,min(limit,100))),
        )
