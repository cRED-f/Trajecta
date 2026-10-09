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
        """Capture a nontrivial finished task once, using existing trajectory evidence.

        Lightweight deterministic extraction deliberately avoids using another
        model or claiming a generated answer succeeded. An LLM reflection pass
        can refine episode summaries in a later, separately reviewed pipeline.
        """
        db = self._provider.sqlite
        if db is None:
            raise RuntimeError("MemoryProvider is not open")
        trajectory = await TrajectoryStore(db).get_with_task(trajectory_id)
        if trajectory is None:
            raise ValueError(f"Unknown trajectory {trajectory_id!r}")
        if trajectory.get("outcome") not in {"completed", "success", "failure"}:
            return None

        existing = await db.fetchone(
            "SELECT * FROM episodes WHERE source_trajectory_id = ?", (trajectory_id,)
        )
        if existing:
            return self._decode(existing)

        events = trajectory.get("steps") or []
        tools: list[str] = []
        errors: list[str] = []
        results = 0
        for event in events:
            if not isinstance(event, dict):
                continue
            kind = str(event.get("type") or event.get("event_type") or "")
            data = event.get("data") or {}
            if not isinstance(data, dict):
                continue
            if kind == "tool.call.delta":
                name = self._text(data.get("name"), 100)
                if name and name not in tools:
                    tools.append(name)
            elif kind == "tool.result":
                results += 1
                name = self._text(data.get("name"), 100)
                if name and name not in tools:
                    tools.append(name)
                if data.get("status") == "error":
                    errors.append(name or "unknown tool")
            elif kind == "run.error":
                errors.append("agent run")
        # Long substantive tool-free tasks may also be useful episodes. Short
        # conversations are already in checkpoint history and should not flood
        # the episodic index.
        goal_raw = trajectory.get("goal") or ""
        answer_raw = trajectory.get("task_result") or ""
        substantial = (len(goal_raw) >= 100 and len(answer_raw) >= 700)
        if results == 0 and not tools and not substantial:
            return None

        goal = self._text(trajectory.get("goal"), 800) or "Task with attachments"
        result = self._text(trajectory.get("task_result"), 750)
        outcome = str(trajectory["outcome"])
        # Neither 'completed' nor legacy 'success' is independent verification.
        verified = False
        summary_parts = [f"Task: {goal}"]
        if tools:
            summary_parts.append(f"Tools used: {', '.join(tools[:20])}")
        if errors:
            summary_parts.append(f"Reported tool errors: {', '.join(dict.fromkeys(errors))}")
        if result:
            summary_parts.append(f"Observed final response: {result}")
        summary_parts.append(f"Recorded outcome: {outcome}; not independently verified")
        summary = "\n".join(summary_parts)[:2200]
        task_meta = trajectory.get("task_metadata") or {}
        if not isinstance(task_meta, dict):
            task_meta = {}
        scope = self.workspace_scope(task_meta.get("workspace_path"))
        user_id = str(trajectory.get("user_id") or "local")
        # Deterministic ID and unique source trajectory make retries idempotent.
        episode_id = hashlib.sha256(f"episode:{trajectory_id}".encode()).hexdigest()
        now = datetime.now(UTC).isoformat()
        evidence = {
            "trajectory_id": trajectory_id,
            "task_id": trajectory.get("task_id"),
            "event_count": len(events),
            "tool_result_count": results,
            "reported_tool_errors": len(errors),
            "source_event_seqs": [
                event["seq"] for event in events
                if isinstance(event, dict) and "seq" in event
                and event.get("type") in {"tool.call.delta", "tool.result", "run.error"}
            ][:100],
            "verification": "unverified",
        }
        await db.execute(
            """INSERT OR IGNORE INTO episodes
                (id, source_trajectory_id, user_id, scope, thread_id,
                 goal, summary, outcome, outcome_verified, tool_names,
                 evidence, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                episode_id, trajectory_id, user_id, scope,
                trajectory.get("thread_id"), goal, summary, outcome, int(verified),
                json.dumps(tools[:20]), json.dumps(evidence), now, now,
            ),
        )
        # Only index a row after SQLite has committed it. Vector search is
        # auxiliary; failure does not erase the authoritative episode record.
        try:
            await asyncio.to_thread(
                self._provider.vector.upsert,
                "episodes", episode_id, summary,
                payload={"user_id": user_id, "scope": scope},
            )
        except Exception:
            logger.warning("Episode vector indexing failed for %s", episode_id, exc_info=True)
        return await self.get(episode_id, user_id=user_id, scope=scope)

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

    def as_tool(self, name: str = "search_past_conversations") -> Any:
        """Expose evidence-linked episode summaries to the model when enabled."""
        from langchain_core.tools import tool

        @tool(name)
        async def search_past_conversations(query: str, limit: int = 5) -> str:
            """Search previous tasks for relevant experiences and outcomes."""
            hits = await self.search(query, limit)
            return "\n\n".join(
                f"[{hit['id']}] outcome={hit['outcome']} "
                f"verified={hit['outcome_verified']} {hit['summary']}"
                for hit in hits
            )

        return search_past_conversations
