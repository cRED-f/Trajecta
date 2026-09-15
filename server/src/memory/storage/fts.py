"""FTSIndex — FTS5 lexical search over memories/episodes.

Standalone FTS5 virtual table kept in sync manually (not via triggers)
because FTS5 requires integer rowids and the memories table uses TEXT ids.
The FTSIndex class manages both the `memories` table and `memories_fts`
virtual table in a single transaction for consistency.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

from server.src.memory.storage.sqlite import SQLiteDatabase


def _fts_rowid(memory_id: str) -> int:
    """Deterministic positive 63-bit FTS5 rowid from a text memory id."""
    return int.from_bytes(
        hashlib.sha256(memory_id.encode()).digest()[:8],
        "big",
    ) & 0x7FFFFFFFFFFFFFFF


class FTSIndex:
    """Lexical full-text index over the `memories` table."""

    def __init__(self, db: SQLiteDatabase) -> None:
        self._db = db

    async def open(self) -> None:
        await self._db.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5("
            "tier UNINDEXED, namespace UNINDEXED, key UNINDEXED, content, tokenize='porter')"
        )

    async def add(self, memory_id: str, tier: str, namespace: str, key: str, content: str) -> None:
        """Insert into memories + FTS in one transaction."""
        now = datetime.now(UTC).isoformat()
        rowid = _fts_rowid(memory_id)
        conn = self._db._conn
        await conn.execute("BEGIN")
        try:
            await conn.execute(
                "INSERT INTO memories (id, tier, namespace, key, content, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (memory_id, tier, namespace, key, content, now, now),
            )
            await conn.execute(
                "INSERT INTO memories_fts(rowid, tier, namespace, key, content) VALUES (?, ?, ?, ?, ?)",
                (rowid, tier, namespace, key, content),
            )
            await conn.execute("COMMIT")
        except Exception:
            await conn.execute("ROLLBACK")
            raise

    async def update(self, memory_id: str, content: str) -> None:
        """Update memories + FTS in one transaction."""
        now = datetime.now(UTC).isoformat()
        rowid = _fts_rowid(memory_id)
        conn = self._db._conn
        await conn.execute("BEGIN")
        try:
            await conn.execute(
                "UPDATE memories SET content = ?, updated_at = ? WHERE id = ?",
                (content, now, memory_id),
            )
            await conn.execute(
                "UPDATE memories_fts SET content = ? WHERE rowid = ?",
                (content, rowid),
            )
            await conn.execute("COMMIT")
        except Exception:
            await conn.execute("ROLLBACK")
            raise

    async def remove(self, memory_id: str) -> None:
        """Delete from memories + FTS in one transaction."""
        rowid = _fts_rowid(memory_id)
        conn = self._db._conn
        await conn.execute("BEGIN")
        try:
            await conn.execute(
                "DELETE FROM memories_fts WHERE rowid = ?",
                (rowid,),
            )
            await conn.execute(
                "DELETE FROM memories WHERE id = ?",
                (memory_id,),
            )
            await conn.execute("COMMIT")
        except Exception:
            await conn.execute("ROLLBACK")
            raise

    async def fetch_memory(self, memory_id: str) -> dict[str, Any] | None:
        """Return the memory row for a given id."""
        return await self._db.fetchone("SELECT * FROM memories WHERE id = ?", (memory_id,))

    async def search(
        self,
        query: str,
        limit: int = 10,
        *,
        tier: str | None = None,
        namespace: str | None = None,
    ) -> list[dict[str, Any]]:
        """FTS search optionally scoped to a memory tier and namespace."""

        limit = max(1, int(limit))

        conditions = [
            "memories_fts MATCH ?",
        ]

        params: list[Any] = [query]

        if tier is not None:
            conditions.append("tier = ?")
            params.append(tier)

        if namespace is not None:
            conditions.append("namespace = ?")
            params.append(namespace)

        params.append(limit)

        where_clause = " AND ".join(conditions)

        return await self._db.fetch(
            f"""
            SELECT
                tier,
                namespace,
                key,
                content,
                snippet(
                    memories_fts,
                    3,
                    '[',
                    ']',
                    '…',
                    24
                ) AS snippet
            FROM memories_fts
            WHERE {where_clause}
            ORDER BY rank
            LIMIT ?
            """,
            tuple(params),
        )