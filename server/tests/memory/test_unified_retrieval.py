"""Offline contracts for scoped retrieval and non-destructive memory curation."""
from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
import pytest_asyncio

from server.src.memory.curator import MemoryCurator
from server.src.memory.episodic.store import EpisodicMemory
from server.src.memory.retrieval import UnifiedMemoryRetriever
from server.src.memory.storage.fts import FTSIndex
from server.src.memory.storage.sqlite import SQLiteDatabase
from server.src.skills.repository import SkillRepository
from server.src.skills.learning.procedures import ProcedureRefinementService
from server.src.skills.representation.skill import Skill, SkillWorkflow
from server.src.skills.trajectory_store.store import TrajectoryStore


class AsyncCursor:
    def __init__(self, cur):
        self._cur = cur
        self.rowcount = cur.rowcount

    async def fetchone(self):
        return self._cur.fetchone()

    async def fetchall(self):
        return self._cur.fetchall()

    async def close(self):
        self._cur.close()


class AsyncConn:
    def __init__(self, path: Path):
        self.raw = sqlite3.connect(path, check_same_thread=False)
        self.raw.row_factory = sqlite3.Row
        self.raw.execute("PRAGMA foreign_keys=ON")

    async def execute(self, sql, params=()):
        return AsyncCursor(self.raw.execute(sql, params))

    async def executescript(self, sql):
        self.raw.executescript(sql)

    async def commit(self):
        self.raw.commit()

    async def close(self):
        self.raw.close()


class Vector:
    def __init__(self):
        self.docs = {"memories": [], "episodes": []}

    def search(self, tier, query, limit):
        return self.docs[tier][:limit]


@pytest_asyncio.fixture
async def env(tmp_path):
    db = SQLiteDatabase(tmp_path / "memory.db")
    db._conn = AsyncConn(db.path)
    await db._migrate()
    fts = FTSIndex(db)
    await fts.open()
    vector = Vector()
    provider = SimpleNamespace(sqlite=db, fts=fts, vector=vector)
    provider.semantic = SimpleNamespace(_memory_id=lambda key: __import__("hashlib").sha256(f"memories:{key}".encode()).hexdigest())
    provider.episodic = EpisodicMemory(provider)
    retriever = UnifiedMemoryRetriever(provider)
    try:
        yield SimpleNamespace(db=db, fts=fts, vector=vector, provider=provider,
                              retrieval=retriever, curator=MemoryCurator(db))
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_schema_migration_survives_reopen(env):
    assert (await env.db.fetchone("SELECT MAX(version) AS version FROM schema_version"))["version"] == 20
    for table in ("memory_usage", "memory_conflicts", "memory_curator_findings"):
        assert await env.db.fetchone("SELECT name FROM sqlite_master WHERE name=?", (table,))
    await env.db.close()
    env.db._conn = AsyncConn(env.db.path)
    await env.db._migrate()
    assert (await env.db.fetchone("SELECT MAX(version) AS version FROM schema_version"))["version"] == 20


@pytest.mark.asyncio
async def test_semantic_vector_authorization_and_usage(env):
    await env.fts.add(env.provider.semantic._memory_id("tool-guidance"), "semantic", "memories",
                      "tool-guidance", "Use independent sources for website research")
    await env.fts.add("other", "semantic", "private", "internal-key", "Secret website research")
    env.vector.docs["memories"] = [{"doc_id": "other", "score": 0.99},
                                   {"doc_id": "fabricated", "score": 0.99}]
    items = await env.retrieval.search("independent sources website research")
    assert any(it["tier"] == "semantic" and it["key"] == "tool-guidance" for it in items)
    assert not any(it.get("key") == "internal-key" for it in items)
    assert not await env.retrieval.search("independent sources website research", user_id="stranger")
    text = await env.retrieval.context("independent sources website research", thread_id="abc")
    assert "independent sources" in text and "untrusted historical data" in text
    assert (await env.db.fetchone("SELECT COUNT(*) AS n FROM memory_usage"))["n"] == 1
    await env.retrieval.context("independent sources website research", thread_id="abc")
    assert (await env.db.fetchone("SELECT COUNT(*) AS n FROM memory_usage"))["n"] == 1


@pytest.mark.asyncio
async def test_episode_workspace_isolation_and_unverified_label(env):
    store = TrajectoryStore(env.db)
    async def make_episode(path):
        _, tid = await store.begin(goal="Investigate blocked website research and try another provider", thread_id="thread", user_id="local", metadata={"workspace_path": path})
        await store.append(tid, event_type="tool.result", data={"name": "web_search", "status": "ok"})
        await store.finish(tid, outcome="completed", result="Result recorded")
        return await env.provider.episodic.consolidate(tid)
    first = await make_episode("/workspace/first")
    second = await make_episode("/workspace/second")
    assert first and second
    env.vector.docs["episodes"] = [{"doc_id": first["id"], "score": 0.9},
                                   {"doc_id": second["id"], "score": 0.9}]
    items = await env.retrieval.search("blocked website research", workspace_path="/workspace/first")
    episodes = [it for it in items if it["tier"] == "episodic"]
    assert [it["id"] for it in episodes] == [first["id"]]
    prompt = await env.retrieval.context("blocked website research", workspace_path="/workspace/first")
    assert "unverified observation" in prompt
    assert not await env.retrieval.search("blocked website research", workspace_path="/workspace/third")


@pytest.mark.asyncio
async def test_conflicts_are_flagged_and_need_explicit_resolution(env):
    import hashlib
    for statement in ("Always verify university admissions", "Never verify university admissions"):
        await env.db.execute(
            """INSERT INTO learned_experiences
               (id,kind,status,scope,fingerprint,content,confidence,version,created_at,updated_at)
               VALUES (?, 'preference', 'active', 'local', ?, ?, .9, 1, ?, ?)""",
            (hashlib.sha256(statement.encode()).hexdigest(), statement,
             statement, datetime.now(UTC).isoformat(), datetime.now(UTC).isoformat()),
        )
    assert not [it for it in await env.retrieval.search("university admissions") if it["tier"] == "experience"]
    conflict = await env.db.fetchone("SELECT * FROM memory_conflicts WHERE status='open'")
    assert conflict and conflict["preferred_ref"] is None
    preferred = conflict["left_ref"]
    await env.db.execute("UPDATE memory_conflicts SET status='resolved', preferred_ref=? WHERE id=?", (preferred, conflict["id"]))
    items = [it for it in await env.retrieval.search("university admissions") if it["tier"] == "experience"]
    assert len(items) == 1 and f"experience:{items[0]['id']}" == preferred


@pytest.mark.asyncio
async def test_only_active_versioned_skills_are_loaded(env):
    repo = SkillRepository(env.db)
    skill = Skill(name="website-research", description="Investigate university website research",
                  instructions="Use trusted sources", workflow=SkillWorkflow(trigger="Investigate university website research"))
    version = await repo.add_version(skill, source_candidate_id=None, source_evaluation_id=None, status="active")
    await repo.set_active(skill, version_id=version["id"])
    results = await env.retrieval.search("university website research")
    assert [(it["name"], it["version"]) for it in results if it["tier"] == "skill"] == [("website-research", skill.version)]
    await repo.set_version_status(version["id"], "staged")
    assert not any(it["tier"] == "skill" for it in await env.retrieval.search("university website research"))


@pytest.mark.asyncio
async def test_curator_finds_stale_archives_only_on_explicit_action(env):
    store = TrajectoryStore(env.db)
    _, tid = await store.begin(goal="Research university documents", thread_id="x")
    await store.finish(tid, outcome="completed", result="done")
    old = (datetime.now(UTC) - timedelta(days=55)).isoformat()
    await env.db.execute(
        """INSERT INTO procedure_drafts
           (id,source_trajectory_id,trigger,user_id,scope,kind,title,steps_json,
            rationale,fingerprint,evidence_json,status,version,user_confirmed,created_at,updated_at)
           VALUES ('d1',?,'feedback','local','local','new','University documents','[]',
                   'Manual review','one','{}','needs_review',1,0,?,?)""",
        (tid, old, old),
    )
    findings = await env.curator.scan(stale_days=30)
    assert any(item["finding_type"] == "stale_proposal" for item in findings)
    assert (await env.db.fetchone("SELECT archived_at FROM procedure_drafts WHERE id='d1'"))["archived_at"] is None
    assert not await env.curator.archive_proposal("d1", scope="workspace:other")
    assert await env.curator.archive_proposal("d1")
    assert (await env.db.fetchone("SELECT status FROM procedure_drafts WHERE id='d1'"))["status"] == "needs_review"
    assert not await env.curator.archive_proposal("d1")
    assert await env.curator.restore_proposal("d1")
    assert (await env.db.fetchone("SELECT archived_at FROM procedure_drafts WHERE id='d1'"))["archived_at"] is None


@pytest.mark.asyncio
async def test_archived_proposal_is_not_reviewable_and_can_be_listed(env):
    store = TrajectoryStore(env.db)
    _, tid = await store.begin(goal="Review reference sources", thread_id="t")
    await store.finish(tid, outcome="completed", result="response")
    now = datetime.now(UTC).isoformat()
    await env.db.execute(
        """INSERT INTO procedure_drafts
           (id,source_trajectory_id,trigger,user_id,scope,kind,title,steps_json,
            rationale,fingerprint,evidence_json,status,version,user_confirmed,created_at,updated_at)
           VALUES ('d2',?,'feedback','local','local','new','References','[]',
                   'Review before use','two','{}','needs_review',1,0,?,?)""",
        (tid, now, now),
    )
    service = ProcedureRefinementService(env.db, store, SkillRepository(env.db))
    assert await env.curator.archive_proposal("d2")
    assert not await service.list()
    assert [item["id"] for item in await service.list(status="archived")] == ["d2"]
    with pytest.raises(ValueError, match="review"):
        await service.review("d2", decision="approve")
    assert await env.curator.restore_proposal("d2")
    assert (await service.review("d2", decision="approve"))["status"] == "approved"
    assert not await env.curator.archive_proposal("d2")


@pytest.mark.asyncio
async def test_unified_api_lists_scoped_results_and_requires_valid_resolution(env):
    import importlib.util
    api_path = Path(__file__).parents[2] / "src/api/routes/memory.py"
    spec = importlib.util.spec_from_file_location("standalone_memory_api", api_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    app = FastAPI()
    app.include_router(module.router, prefix="/api/v1")
    app.state.memory_provider = env.provider
    app.state.memory_retriever = env.retrieval
    app.state.memory_curator = env.curator
    with TestClient(app) as client:
        assert client.get("/api/v1/memory/unified/search", params={"query": "research"}).status_code == 200
        assert client.get("/api/v1/memory/curator/findings").status_code == 200
        assert client.post("/api/v1/memory/curator/scan").status_code == 200
        assert client.get("/api/v1/memory/unified/conflicts").status_code == 200
        assert client.post("/api/v1/memory/unified/conflicts/987/resolve",
                           json={"preferred_ref": "experience:nonexistent"}).status_code == 404
