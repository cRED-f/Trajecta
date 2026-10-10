"""Durable episodic memory distilled from observed task trajectories.

Episodes are indexed summaries with pointers to original evidence, not a copy of
model reasoning or a claim that task completion implies verified success.
SQLite remains authoritative; Qdrant is an optional, repairable search index.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from server.src.memory.storage.fts import _fts_query
from server.src.output_safety import contains_internal_context
from server.src.skills.trajectory_store import TrajectoryStore

if TYPE_CHECKING:
    from server.src.memory.provider import MemoryProvider

logger = logging.getLogger(__name__)


class EpisodicMemory:
    """Index and retrieve evidence-linked episodes, scoped by user and workspace."""

    def __init__(self, provider: "MemoryProvider") -> None:
        self._provider = provider
        self._user_id = "local"

    def set_user(self, user_id: str) -> None:
        if not user_id.strip():
            raise ValueError("user_id cannot be empty")
        self._user_id = user_id

    @staticmethod
    def workspace_scope(path: str | None) -> str:
        """Use a stable non-reversible workspace identifier for isolation."""
        if not path:
            return "local"
        digest = hashlib.sha256(path.encode("utf-8")).hexdigest()[:24]
        return f"workspace:{digest}"

    @staticmethod
    def _text(value: Any, limit: int = 500) -> str:
        """Avoid indexing unbounded tool outputs or raw tool arguments."""
        if not isinstance(value, str):
            return ""
        text = " ".join(value.split())[:limit]
        text = re.sub(
            r"(?i)\b(api[_-]?key|access[_-]?token|secret|password)\s*[:=]\s*\S+",
            r"\1=[REDACTED]", text,
        )
        text = re.sub(r"\b(?:sk-(?:proj-)?|ghp_|gho_)[A-Za-z0-9_-]{12,}", "[REDACTED]", text)
        return re.sub(r"(?i)\bBearer\s+[A-Za-z0-9._-]{8,}", "Bearer [REDACTED]", text)

    @staticmethod
    def _decode(row: dict[str, Any]) -> dict[str, Any]:
        item = dict(row)
        item["tool_names"] = json.loads(item.get("tool_names") or "[]")
        item["evidence"] = json.loads(item.get("evidence") or "{}")
        item["outcome_verified"] = bool(item.get("outcome_verified"))
        return item

    async def consolidate(self, trajectory_id: str) -> dict[str, Any] | None:
        """Consolidate linked runs into ONE task episode, including later revisions.

        Called only by the durable background worker. Never use the existence
        of a final answer or positive rating as independent success proof.
        """
        db = self._provider.sqlite
        if db is None:
            raise RuntimeError("MemoryProvider is not open")
        store = TrajectoryStore(db)
        trace = await store.get_with_task(trajectory_id)
        if trace is None:
            raise ValueError(f"Unknown trajectory {trajectory_id!r}")
        task_id = str(trace["task_id"])
        traces = await store.for_task(task_id, limit=40)
        finished = [run for run in traces if run.get("outcome") in {"completed", "success", "failure", "cancelled"}]
        if not finished:
            return None
        tools: list[str] = []
        errors: list[str] = []
        evidence_refs: list[dict[str, Any]] = []
        total_results = 0
        for run in finished:
            for event in run.get("steps") or []:
                if not isinstance(event, dict):
                    continue
                kind = str(event.get("type") or "")
                data = event.get("data") or {}
                if not isinstance(data, dict):
                    data = {}
                if kind in {"tool.call.delta", "tool.result", "run.error"}:
                    seq = event.get("seq")
                    if isinstance(seq, int) and len(evidence_refs) < 150:
                        evidence_refs.append({"trajectory_id": run["id"], "seq": seq})
                if kind.startswith("tool."):
                    name = self._text(data.get("name"), 90)
                    if name and name not in tools:
                        tools.append(name)
                if kind == "tool.result":
                    total_results += 1
                    if data.get("status") == "error":
                        errors.append(self._text(data.get("name"), 90) or "tool error")
                if kind == "run.error":
                    errors.append("agent run")
        task_goal = self._text(trace.get("task_goal") or trace.get("goal"), 800)
        substantial = any(len(str(t.get("metadata",{}).get("user_feedback_note") or "")) > 60
                          or len(str(t.get("goal") or "")) >= 100
                          for t in finished)
        if not tools and not substantial and not errors:
            return None
        last = finished[-1]
        raw_result = str(last.get("task_result") or "")
        result = ("[internal-context response omitted]" if contains_internal_context(raw_result)
                  else self._text(raw_result, 650))
        outcome = str(last.get("outcome") or "unknown")
        feedback = (last.get("metadata") or {}).get("user_feedback")
        if feedback not in {"success", "failure"}:
            feedback = None
        summary = "\n".join([
            f"Task: {task_goal or 'Task with attachments'}",
            f"Observed runs: {len(finished)}",
            *([f"Tools: {', '.join(tools[:20])}"] if tools else []),
            *([f"Observed errors: {', '.join(dict.fromkeys(errors))}"] if errors else []),
            *([f"Latest observed response: {result}"] if result else []),
            f"Outcome: {outcome}; not independently verified",
        ])[:2500]
        task_meta = trace.get("task_metadata") or {}
        if not isinstance(task_meta, dict):
            task_meta = {}
        scope = self.workspace_scope(task_meta.get("workspace_path"))
        user_id = str(trace.get("user_id") or "local")
        eid = hashlib.sha256(f"task-episode:{task_id}".encode()).hexdigest()
        evidence = {"task_id": task_id, "trajectory_ids": [t["id"] for t in finished],
                    "event_refs": evidence_refs, "tool_result_count": total_results,
                    "reported_tool_errors": len(errors), "verification": "unverified",
                    "user_feedback": feedback}
        current = datetime.now(UTC).isoformat()
        existing = await db.fetchone("SELECT * FROM episodes WHERE logical_task_id=?", (task_id,))
        if existing:
            await db.execute(
                """UPDATE episodes SET summary=?,outcome=?,tool_names=?,evidence=?,updated_at=?
                   WHERE id=?""",
                (summary, outcome, json.dumps(tools), json.dumps(evidence), current, existing["id"]),
            )
        else:
            await db.execute(
                """INSERT OR IGNORE INTO episodes
                   (id,source_trajectory_id,logical_task_id,user_id,scope,thread_id,
                    goal,summary,outcome,outcome_verified,tool_names,evidence,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,0,?,?,?,?)""",
                (eid, finished[0]["id"], task_id, user_id, scope, trace.get("thread_id"),
                 task_goal or "Task with attachments", summary, outcome, json.dumps(tools),
                 json.dumps(evidence), current, current),
            )
        try:
            await asyncio.to_thread(self._provider.vector.upsert, "episodes", eid,
                summary, payload={"user_id": user_id, "scope": scope})
        except Exception:
            logger.warning("Task episode vector indexing failed", exc_info=True)
        return await self.get(eid, user_id=user_id, scope=scope)

    async def get(
        self, episode_id: str, *, user_id: str | None = None, scope: str = "local"
    ) -> dict[str, Any] | None:
        db = self._provider.sqlite
        if db is None:
            raise RuntimeError("MemoryProvider is not open")
        row = await db.fetchone(
            "SELECT * FROM episodes WHERE id = ? AND user_id = ? AND scope = ?",
            (episode_id, user_id or self._user_id, scope),
        )
        return self._decode(row) if row else None

    async def list(
        self, *, limit: int = 50, user_id: str | None = None, scope: str | None = None
    ) -> list[dict[str, Any]]:
        db = self._provider.sqlite
        if db is None:
            raise RuntimeError("MemoryProvider is not open")
        if scope is None:
            rows = await db.fetch(
                "SELECT * FROM episodes WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
                (user_id or self._user_id, max(1, min(limit, 500))),
            )
        else:
            rows = await db.fetch(
                """SELECT * FROM episodes WHERE user_id = ? AND scope = ?
                   ORDER BY created_at DESC LIMIT ?""",
                (user_id or self._user_id, scope, max(1, min(limit, 500))),
            )
        return [self._decode(row) for row in rows]

    async def search(
        self, query: str, limit: int = 10, *,
        user_id: str | None = None, scope: str = "local",
    ) -> list[dict[str, Any]]:
        """Hybrid RRF retrieval with authorization enforced in SQLite.

        Scope defaults to local, never a cross-user scan. Vector payloads are
        untrusted candidates: SQLite checks the user/scope for every returned ID.
        """
        db = self._provider.sqlite
        if db is None:
            raise RuntimeError("MemoryProvider is not open")
        uid = user_id or self._user_id
        limit = max(1, min(int(limit), 100))
        match = _fts_query(query)
        if match == '""':
            return []
        lexical = await db.fetch(
            """SELECT e.id FROM episodes_fts JOIN episodes e ON e.seq = episodes_fts.rowid
               WHERE episodes_fts MATCH ? AND e.user_id = ? AND e.scope = ?
               ORDER BY bm25(episodes_fts) LIMIT ?""",
            (match, uid, scope, limit * 4),
        )
        scores: dict[str, float] = {}
        for rank, row in enumerate(lexical, 1):
            scores[row["id"]] = scores.get(row["id"], 0.0) + 1.0 / (40 + rank)
        try:
            vectors = await asyncio.to_thread(
                self._provider.vector.search, "episodes", query, limit * 6
            )
        except Exception:
            logger.warning("Episode vector search failed", exc_info=True)
            vectors = []
        for rank, hit in enumerate(vectors, 1):
            # Nearest-neighbor APIs return a result even if similarity is weak.
            # A conservative floor avoids turning irrelevant memories into hits.
            if float(hit.get("score") or 0.0) < 0.35:
                continue
            doc_id = str(hit.get("doc_id") or "")
            if doc_id:
                scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (40 + rank)
        if not scores:
            return []
        # Never trust user_id/scope from Qdrant payload alone.
        ids = sorted(scores, key=scores.get, reverse=True)[:500]
        placeholders = ",".join("?" for _ in ids)
        rows = await db.fetch(
            f"SELECT * FROM episodes WHERE id IN ({placeholders}) AND user_id = ? AND scope = ?",
            (*ids, uid, scope),
        )
        allowed = {row["id"]: row for row in rows}
        return [
            {**self._decode(allowed[episode_id]), "score": round(scores[episode_id], 6)}
            for episode_id in ids if episode_id in allowed
        ][:limit]

    async def delete(
        self, episode_id: str, *, user_id: str | None = None, scope: str = "local"
    ) -> bool:
        db = self._provider.sqlite
        if db is None:
            raise RuntimeError("MemoryProvider is not open")
        row = await self.get(episode_id, user_id=user_id, scope=scope)
        if row is None:
            return False
        await db.execute(
            "DELETE FROM episodes WHERE id = ? AND user_id = ? AND scope = ?",
            (episode_id, user_id or self._user_id, scope),
        )
        await asyncio.to_thread(self._provider.vector.delete, "episodes", episode_id)
        return True

    async def sync_feedback(self, trajectory_id: str, rating: str) -> dict[str, Any] | None:
        """Sync explicit user rating without asserting independent verification."""
        if rating not in {"success", "failure"}:
            raise ValueError("invalid feedback")
        db = self._provider.sqlite
        if db is None:
            raise RuntimeError("MemoryProvider is not open")
        row = await db.fetchone("SELECT * FROM episodes WHERE source_trajectory_id=?", (trajectory_id,))
        if row is None:
            return None
        evidence = json.loads(row.get("evidence") or "{}")
        evidence["user_feedback"] = rating
        # Retain the original observed response; change only outcome annotation.
        summary = re.sub(r"\nRecorded outcome: [^\n]*$", "", row["summary"])
        summary += (f"\nRecorded outcome: {rating} (explicit user feedback); "
                    "not independently verified")
        now = datetime.now(UTC).isoformat()
        await db.execute(
            """UPDATE episodes SET outcome=?, summary=?, evidence=?, updated_at=?
               WHERE id=?""",
            (rating, summary, json.dumps(evidence), now, row["id"]),
        )
        try:
            await asyncio.to_thread(
                self._provider.vector.upsert, "episodes", row["id"], summary,
                payload={"user_id": row["user_id"], "scope": row["scope"]},
            )
        except Exception:
            logger.warning("Episode vector refresh failed for %s", row["id"], exc_info=True)
        return await self.get(row["id"], user_id=row["user_id"], scope=row["scope"])

    def as_tool(
        self, name: str = "search_past_conversations", *,
        user_id: str = "local", scope: str = "local",
    ) -> Any:
        """Expose evidence-linked episode summaries to the model when enabled."""
        from langchain_core.tools import tool

        @tool(name)
        async def search_past_conversations(query: str, limit: int = 5) -> str:
            """Search previous tasks for relevant experiences and outcomes."""
            hits = await self.search(query, max(1, min(limit, 10)), user_id=user_id, scope=scope)
            return "\n\n".join(
                f"[{hit['id']}] outcome={hit['outcome']} "
                f"verified={hit['outcome_verified']} {hit['summary']}"
                for hit in hits
            )

        return search_past_conversations
