"""Conservative memory curator: detect review needs without silent deletion.

Read/write APIs are explicitly invoked by a human. Scan only raises findings;
it NEVER changes active skills, memory, or workflow permissions. Archiving a
pending proposal is an explicit, reversible action with an audit record.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from server.src.memory.storage.sqlite import SQLiteDatabase


def _now() -> str:
    return datetime.now(UTC).isoformat()


class MemoryCurator:
    def __init__(self, db: SQLiteDatabase) -> None:
        self.db = db

    async def scan(self, *, user_id: str = "local", scope: str = "local",
                   stale_days: int = 30, limit: int = 100) -> list[dict[str, Any]]:
        """Scan registry and pending proposals; no changes to their statuses."""
        if user_id != "local":
            return []
        cutoff = (datetime.now(UTC) - timedelta(days=max(7, stale_days))).isoformat()
        candidates = await self.db.fetch(
            """SELECT d.id, d.title, d.created_at FROM procedure_drafts d
               WHERE d.user_id=? AND d.scope=? AND d.status='needs_review'
                 AND d.archived_at IS NULL AND d.created_at < ? ORDER BY d.created_at LIMIT ?""",
            (user_id, scope, cutoff, min(max(limit, 1), 200)),
        )
        findings = [("stale_proposal", "procedure", str(d["id"]),
                     "Pending procedure has not been reviewed; consider archiving or reviewing it.")
                    for d in candidates]
        # Exact duplicate evidence fingerprints are a review signal, not
        # proof that the procedure steps are equally correct.
        duplicates = await self.db.fetch(
            """SELECT d.id FROM procedure_drafts d
               WHERE d.user_id=? AND d.scope=? AND d.status='needs_review'
                 AND d.archived_at IS NULL AND EXISTS (
                   SELECT 1 FROM procedure_drafts p
                   WHERE p.user_id=d.user_id AND p.scope=d.scope
                     AND p.fingerprint=d.fingerprint AND p.id<>d.id
                     AND p.created_at<d.created_at
                     AND p.status='needs_review' AND p.archived_at IS NULL)
               ORDER BY d.created_at LIMIT ?""",
            (user_id, scope, min(max(limit, 1), 200)),
        )
        findings.extend(("duplicate_proposal", "procedure", str(d["id"]),
                         "Another pending proposal has identical evidence; review before consolidating.")
                        for d in duplicates)
        if scope == "local":
            skills = await self.db.fetch(
                """SELECT s.name FROM skills s WHERE s.status='active'
                   AND s.created_at < ? AND NOT EXISTS (
                       SELECT 1 FROM memory_usage u WHERE u.tier='skill'
                         AND u.item_id=s.name AND u.version=s.version AND u.user_id='local' AND u.scope='local' AND u.last_retrieved_at >= ?)
                   ORDER BY s.name LIMIT ?""",
                (cutoff, cutoff, min(max(limit, 1), 200)),
            )
            findings.extend(("unused_skill", "skill", str(s["name"]),
                             "Active skill has no recent recorded context retrieval. Verify usefulness before archiving.")
                            for s in skills)
        now = _now()
        for kind, tier, item_id, summary in findings[:limit]:
            await self.db.execute(
                """INSERT OR IGNORE INTO memory_curator_findings
                    (finding_type,item_type,item_id,user_id,scope,summary,created_at,updated_at)
                    VALUES (?,?,?,?,?,?,?,?)""",
                (kind, tier, item_id, user_id, scope, summary, now, now),
            )
        return await self.list(user_id=user_id, scope=scope, limit=limit)

    async def list(self, *, user_id: str = "local", scope: str = "local",
                   limit: int = 100) -> list[dict[str, Any]]:
        return await self.db.fetch(
            """SELECT * FROM memory_curator_findings WHERE user_id=? AND scope=?
               ORDER BY created_at DESC LIMIT ?""",
            (user_id, scope, min(max(limit, 1), 200)),
        )

    async def dismiss(self, finding_id: int, *, user_id: str = "local", scope: str = "local") -> bool:
        cur = await self.db.execute(
            """UPDATE memory_curator_findings SET status='dismissed', updated_at=?
               WHERE id=? AND user_id=? AND scope=? AND status='open'""",
            (_now(), finding_id, user_id, scope),
        )
        return cur.rowcount == 1

    async def archive_proposal(self, draft_id: str, *, user_id: str = "local", scope: str = "local") -> bool:
        """Reversible human-triggered archive of a *pending* proposal only."""
        cur = await self.db.execute(
            """UPDATE procedure_drafts SET archived_at=?, updated_at=?
               WHERE id=? AND user_id=? AND scope=? AND status='needs_review'
                 AND archived_at IS NULL""",
            (_now(), _now(), draft_id, user_id, scope),
        )
        if cur.rowcount:
            await self.db.execute(
                """UPDATE memory_curator_findings SET status='resolved', updated_at=?
                   WHERE item_type='procedure' AND item_id=? AND user_id=? AND scope=?""",
                (_now(), draft_id, user_id, scope),
            )
        return cur.rowcount == 1

    async def restore_proposal(self, draft_id: str, *, user_id: str = "local", scope: str = "local") -> bool:
        cur = await self.db.execute(
            """UPDATE procedure_drafts SET archived_at=NULL, updated_at=?
               WHERE id=? AND user_id=? AND scope=? AND status='needs_review'
                 AND archived_at IS NOT NULL""",
            (_now(), draft_id, user_id, scope),
        )
        return cur.rowcount == 1
