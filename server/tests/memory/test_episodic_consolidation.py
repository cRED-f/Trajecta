"""Offline contract tests for evidence-linked episodic memory."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.src.api.routes.memory import router
from server.src.memory.episodic.store import EpisodicMemory
from server.src.memory.storage.sqlite import SQLiteDatabase
from server.src.skills.trajectory_store import TrajectoryStore


class AsyncCursor:
    def __init__(self, cur):
        self.cur = cur

    async def fetchone(self):
        return self.cur.fetchone()

    async def fetchall(self):
        return self.cur.fetchall()


class AsyncSQLite:
    """SQLite's real FTS/trigger engine behind an async-compatible test adapter."""

    def __init__(self, path: Path):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")

    async def execute(self, sql, args=()):
        return AsyncCursor(self.conn.execute(sql, args))

    async def executescript(self, sql):
        self.conn.executescript(sql)

    async def commit(self):
        self.conn.commit()

    async def close(self):
        self.conn.close()


class FakeVector:
    def __init__(self):
        self.data = {}

    def upsert(self, namespace, doc_id, text, *, payload=None):
        self.data[(namespace, doc_id)] = {"doc_id": doc_id, "text": text, "payload": payload, "score": 0.85}

    def delete(self, namespace, doc_id):
        self.data.pop((namespace, doc_id), None)

    def search(self, namespace, query, limit):
        # Simulate Qdrant returning matches across users and scopes. The
        # episodic store must re-check authorization using SQLite.
        return [v for (ns, _), v in self.data.items() if ns == namespace][:limit]


@pytest_asyncio.fixture
async def memory(tmp_path):
    db = SQLiteDatabase(tmp_path / "trajecta.db")
    db._conn = AsyncSQLite(db.path)
    await db._migrate()
    vector = FakeVector()
    provider = SimpleNamespace(sqlite=db, vector=vector)
    episodic = EpisodicMemory(provider)
    try:
        yield db, vector, episodic
    finally:
        await db.close()


async def completed_run(db, *, goal="Investigate 403 web extraction", user_id="local", workspace=None):
    trajectories = TrajectoryStore(db)
    _, tid = await trajectories.begin(
        goal=goal, thread_id="thread-1", user_id=user_id,
        metadata={"workspace_path": workspace},
    )
    await trajectories.append(tid, event_type="tool.call.delta", data={"name": "web_extract"})
    await trajectories.append(
        tid, event_type="tool.result",
        data={"name": "web_extract", "status": "error", "content": "403 secret_token"},
    )
    await trajectories.append(tid, event_type="tool.call.delta", data={"name": "web_search"})
    await trajectories.append(
        tid, event_type="tool.result",
        data={"name": "web_search", "status": "success", "content": "alternative"},
    )
    await trajectories.finish(tid, outcome="completed", result="Found a credible replacement source")
    return tid


@pytest.mark.asyncio
async def test_migration_consolidation_search_and_restart(memory):
    db, vector, ep = memory
    tid = await completed_run(db)
    result = await ep.consolidate(tid)
    assert result is not None
    assert result["source_trajectory_id"] == tid
    assert result["outcome_verified"] is False
    assert result["tool_names"] == ["web_extract", "web_search"]
    assert "403 secret_token" not in result["summary"]
    assert result["evidence"]["tool_result_count"] == 2
    assert len(await ep.list()) == 1
    assert (await ep.consolidate(tid))["id"] == result["id"]
    assert len(await ep.list()) == 1
    hits = await ep.search('403 alternative web')
    assert any(h["id"] == result["id"] for h in hits)
    assert await ep.search('" OR DELETE:token') is not None
    await db.close()
    db._conn = AsyncSQLite(db.path)
    await db._migrate()
    assert (await EpisodicMemory(SimpleNamespace(sqlite=db, vector=vector)).list())[0]["id"] == result["id"]


@pytest.mark.asyncio
async def test_skip_interrupted_and_trivial_and_scope_isolation(memory):
    db, vector, ep = memory
    store = TrajectoryStore(db)
    _, tid = await store.begin(goal="Hi", thread_id="t")
    await store.finish(tid, outcome="completed", result="Hello")
    assert await ep.consolidate(tid) is None
    _, paused = await store.begin(goal="Run a tool", thread_id="t")
    await store.append(paused, event_type="tool.result", data={"name": "run"})
    await store.finish(paused, outcome="interrupted")
    assert await ep.consolidate(paused) is None

    alice = await completed_run(db, user_id="alice", workspace="/some/workspace")
    alice_episode = await ep.consolidate(alice)
    assert alice_episode is not None
    assert await ep.get(alice_episode["id"], user_id="local") is None
    assert not await ep.search("web_extract", user_id="local")
    assert not await ep.search("web_extract", user_id="alice", scope="local")
    scope = ep.workspace_scope("/some/workspace")
    assert await ep.get(alice_episode["id"], user_id="alice", scope=scope)
    assert not await ep.delete(alice_episode["id"], user_id="local", scope=scope)
    assert await ep.delete(alice_episode["id"], user_id="alice", scope=scope)
    assert await ep.get(alice_episode["id"], user_id="alice", scope=scope) is None
    # The FTS trigger removes the deleted episode from lexical search.
    assert not await ep.search("Investigate", user_id="alice", scope=scope)


@pytest.mark.asyncio
async def test_memory_api_endpoints(memory):
    db, vector, ep = memory
    tid = await completed_run(db)
    item = await ep.consolidate(tid)
    provider = SimpleNamespace(sqlite=db, vector=vector, episodic=ep)
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.state.memory_provider = provider
    with TestClient(app) as client:
        listing = client.get("/api/v1/memory/episodic")
        assert listing.status_code == 200
        assert len(listing.json()) == 1
        search = client.get("/api/v1/memory/episodic/search", params={"query": "web extraction"})
        assert search.status_code == 200
        assert search.json()[0]["id"] == item["id"]
        assert client.get(f"/api/v1/memory/episodic/{item['id']}").status_code == 200
        assert client.delete(f"/api/v1/memory/episodic/{item['id']}").status_code == 200
        assert client.get(f"/api/v1/memory/episodic/{item['id']}").status_code == 404


@pytest.mark.asyncio
async def test_substantial_tool_free_work_and_failed_runs(memory):
    db, _, ep = memory
    store = TrajectoryStore(db)
    _, deep = await store.begin(goal="Research topic " * 15, thread_id="t")
    await store.finish(deep, outcome="completed", result="Detailed conclusion. " * 55)
    episode = await ep.consolidate(deep)
    assert episode is not None
    assert episode["tool_names"] == []
    assert episode["outcome_verified"] is False

    _, failed = await store.begin(goal="Attempt a download", thread_id="t")
    await store.append(failed, event_type="tool.call.delta", data={"name": "web_extract"})
    await store.append(failed, event_type="run.error", data={"error": "HTTP 403"})
    await store.finish(failed, outcome="failure", result="Unable to download")
    failing_episode = await ep.consolidate(failed)
    assert failing_episode is not None
    assert failing_episode["outcome"] == "failure"
    assert "agent run" in failing_episode["summary"]


@pytest.mark.asyncio
async def test_episode_redacts_credentials(memory):
    db, _, ep = memory
    store = TrajectoryStore(db)
    _, tid = await store.begin(
        goal="Fix integration with API_KEY=topsecret123 and Bearer abcdefghijklmno",
        thread_id="t",
    )
    await store.append(tid, event_type="tool.result", data={"name": "test_api"})
    await store.finish(tid, outcome="completed", result="Used sk-proj-abcdefghijklmnopqrstuvwxyz0000")
    episode = await ep.consolidate(tid)
    assert episode is not None
    assert "topsecret123" not in episode["summary"]
    assert "abcdefghijklmno" not in episode["summary"]
    assert "abcdefghijklmnopqrstuvwxyz0000" not in episode["summary"]
