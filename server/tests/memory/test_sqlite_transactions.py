"""Regression tests for overlapping chat/background SQLite memory writes.

The fake exposes the same async methods as aiosqlite while using real SQLite,
so the tests reproduce BEGIN/COMMIT behavior without starting Ollama/Qdrant.
"""
from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from server.src.memory.storage.fts import FTSIndex
from server.src.memory.storage.sqlite import SQLiteDatabase


class AsyncCursor:
    def __init__(self, cursor: sqlite3.Cursor) -> None:
        self._cursor = cursor

    async def fetchone(self):
        return self._cursor.fetchone()

    async def fetchall(self):
        return self._cursor.fetchall()

    async def close(self) -> None:
        self._cursor.close()


class AsyncConnection:
    def __init__(self, path: Path) -> None:
        self.raw = sqlite3.connect(path, check_same_thread=False)
        self.raw.row_factory = sqlite3.Row
        self.raw.execute("PRAGMA foreign_keys=ON")
        self.pause_sql: str | None = None
        self.paused = asyncio.Event()
        self.release = asyncio.Event()

    async def execute(self, sql, params=()):
        cursor = self.raw.execute(sql, params)
        if self.pause_sql is not None and self.pause_sql in sql:
            self.paused.set()
            await self.release.wait()
        return AsyncCursor(cursor)

    async def executescript(self, sql):
        self.raw.executescript(sql)

    async def commit(self):
        self.raw.commit()

    async def rollback(self):
        self.raw.rollback()

    async def close(self):
        self.raw.close()


@pytest.fixture
async def database(tmp_path):
    db = SQLiteDatabase(tmp_path / "memory.sqlite")
    conn = AsyncConnection(db.path)
    db._conn = conn
    await db._migrate()
    fts = FTSIndex(db)
    await fts.open()
    try:
        yield db, fts, conn
    finally:
        conn.release.set()
        await db.close()


@pytest.mark.asyncio
async def test_memory_write_waits_for_concurrent_sqlite_writer(database):
    db, fts, conn = database
    await db.execute("CREATE TABLE concurrency_probe (value TEXT NOT NULL)")
    conn.pause_sql = "INSERT INTO concurrency_probe"
    writer = asyncio.create_task(db.execute("INSERT INTO concurrency_probe(value) VALUES (?)", ("started",)))
    await asyncio.wait_for(conn.paused.wait(), timeout=1)

    # SQLite already has an implicit transaction from writer.  Before this
    # fix, FTSIndex.add issued BEGIN directly and failed here.
    saving = asyncio.create_task(fts.add("memory-a", "semantic", "memories", "education", "BSc in CSE"))
    await asyncio.sleep(0)
    assert not saving.done()
    conn.release.set()
    await asyncio.wait_for(asyncio.gather(writer, saving), timeout=2)

    assert (await fts.fetch_memory("memory-a"))["content"] == "BSc in CSE"
    assert (await db.fetchone("SELECT COUNT(*) AS n FROM memories_fts"))["n"] == 1
    assert not conn.raw.in_transaction


@pytest.mark.asyncio
async def test_upsert_same_memory_key_is_idempotent(database):
    db, fts, conn = database
    await asyncio.gather(*(
        fts.upsert("memory-a", "semantic", "memories", "education", f"value {i}")
        for i in range(15)
    ))
    memory = await fts.fetch_memory("memory-a")
    assert memory is not None
    assert memory["content"].startswith("value ")
    assert (await db.fetchone("SELECT COUNT(*) AS n FROM memories WHERE id = ?", ("memory-a",)))["n"] == 1
    assert (await db.fetchone("SELECT COUNT(*) AS n FROM memories_fts"))["n"] == 1
    fts_row = await db.fetchone("SELECT content FROM memories_fts")
    assert fts_row["content"] == memory["content"]
    assert not conn.raw.in_transaction


@pytest.mark.asyncio
async def test_failed_fts_write_rolls_back_and_allows_future_writes(database):
    db, fts, conn = database
    await db.execute("DROP TABLE memories_fts")
    with pytest.raises(sqlite3.OperationalError):
        await fts.add("should-rollback", "semantic", "memories", "foo", "bar")
    assert await fts.fetch_memory("should-rollback") is None
    assert not conn.raw.in_transaction
    await db.execute("CREATE TABLE healthy_after_rollback (id INTEGER)")
    await db.execute("INSERT INTO healthy_after_rollback VALUES (1)")
    assert (await db.fetchone("SELECT COUNT(*) AS n FROM healthy_after_rollback"))["n"] == 1


@pytest.mark.asyncio
async def test_cancelled_fts_transaction_is_rolled_back(database):
    db, _, conn = database
    # A cancellation after BEGIN must roll back and release the connection.
    entered = asyncio.Event()

    async def cancelled_write():
        async with db.transaction() as raw:
            await raw.execute(
                "INSERT INTO memories(id,tier,namespace,key,content,created_at,updated_at) "
                "VALUES ('cancelled','semantic','memories','x','y','now','now')"
            )
            entered.set()
            await asyncio.sleep(10)

    task = asyncio.create_task(cancelled_write())
    await asyncio.wait_for(entered.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await db.fetchone("SELECT * FROM memories WHERE id='cancelled'") is None
    assert not conn.raw.in_transaction
    await db.execute("INSERT INTO memories(id,tier,namespace,key,content,created_at,updated_at) "
                     "VALUES ('after','semantic','memories','x','ok','now','now')")
    assert (await db.fetchone("SELECT content FROM memories WHERE id='after'"))["content"] == "ok"


@pytest.mark.asyncio
async def test_failed_single_statement_rolls_back_before_next_fts_begin(database):
    db, fts, conn = database
    await db.execute("CREATE TABLE unique_probe(value TEXT UNIQUE)")
    await db.execute("INSERT INTO unique_probe VALUES (?)", ("duplicate",))
    with pytest.raises(sqlite3.IntegrityError):
        await db.execute("INSERT INTO unique_probe VALUES (?)", ("duplicate",))
    assert not conn.raw.in_transaction
    await fts.upsert("after-error", "semantic", "memories", "key", "Still works")
    assert (await fts.fetch_memory("after-error"))["content"] == "Still works"
