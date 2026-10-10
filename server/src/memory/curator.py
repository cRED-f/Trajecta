"""Memory-maintenance diagnostics and non-destructive usage findings.

Old proposal review endpoints are retired. A finding cannot activate or
change tool permissions. Historical audit records remain readable.
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
        """Scan skill usage without modifying active versions."""
        if user_id != "local":
            return []
        cutoff = (datetime.now(UTC) - timedelta(days=max(7, stale_days))).isoformat()
        # Legacy manual procedure queues are no longer scanned. Existing rows
        # are preserved as historical evidence, not operational suggestions.
        findings: list[tuple[str,str,str,str]] = []
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

