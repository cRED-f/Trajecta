"""SQLite database — Trajecta's own store (not Deep Agents memory).

Owns task metadata, trajectory metadata, skill registry, and config records.
Separate from `langgraph.db`, which LangGraph owns (checkpointer + long-term store).
Lives under `.trajecta/data/trajecta.db`.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

# aiosqlite exists as a dependency; import lazily so a missing wheel breaks
# only the code path that needs it, not the whole package import.


class SQLiteDatabase:
    """Async SQLite wrapper with migrations and connection pool.

    Usage:
        db = SQLiteDatabase(path)
        await db.open()
        await db.execute(...)
        rows = await db.fetch(...)
        await db.close()
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._conn: Any | None = None  # aiosqlite.Connection

    @property
    def path(self) -> Path:
        return self._path

    async def open(self) -> None:
        import aiosqlite

        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self._path)
        self._conn.row_factory = sqlite3.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._migrate()

    async def _migrate(self) -> None:
        """Create schema if missing; track schema version."""
        assert self._conn is not None
        await self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER NOT NULL
            )
            """
        )
        row = await self._conn.execute("SELECT version FROM schema_version ORDER BY version DESC LIMIT 1")
        result = await row.fetchone()
        version = result["version"] if result else 0

        if version < 1:
            await self._migrate_v1()
            await self._conn.execute("INSERT INTO schema_version (version) VALUES (1)")
        await self._conn.commit()

    async def _migrate_v1(self) -> None:
        """Schema v1: tasks, trajectories, skills, memories metadata."""
        assert self._conn is not None
        await self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                thread_id TEXT,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                goal TEXT,
                plan TEXT,
                result TEXT,
                metadata TEXT
            );

            CREATE TABLE IF NOT EXISTS trajectories (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL REFERENCES tasks(id),
                created_at TEXT NOT NULL,
                steps TEXT,       -- JSON list of execution steps
                outcome TEXT,     -- success / failure
                metadata TEXT
            );

            CREATE TABLE IF NOT EXISTS skills (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                version TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'candidate',
                path TEXT,
                created_at TEXT NOT NULL,
                metadata TEXT
            );

            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                tier TEXT NOT NULL,          -- semantic / episodic / procedural
                namespace TEXT NOT NULL,
                key TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_tasks_user ON tasks(user_id);
            CREATE INDEX IF NOT EXISTS idx_trajectories_task ON trajectories(task_id);
            CREATE INDEX IF NOT EXISTS idx_memories_tier ON memories(tier);
            CREATE INDEX IF NOT EXISTS idx_memories_ns ON memories(namespace);
            """
        )

    # -- execution helpers -------------------------------------------------

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> Any:
        """Run a single SQL statement. Use `executescript` for multi-statement DDL."""
        assert self._conn is not None, "call open() first"
        cur = await self._conn.execute(sql, params)
        await self._conn.commit()
        return cur

    async def executescript(self, sql: str) -> None:
        """Run a multi-statement SQL script (DDL with several statements)."""
        assert self._conn is not None, "call open() first"
        await self._conn.executescript(sql)
        await self._conn.commit()

    async def fetch(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        assert self._conn is not None, "call open() first"
        cur = await self._conn.execute(sql, params)
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def fetchone(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        rows = await self.fetch(sql, params)
        return rows[0] if rows else None

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def __aenter__(self) -> "SQLiteDatabase":
        await self.open()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()