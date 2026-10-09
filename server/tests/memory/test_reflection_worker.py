"""Offline integration contracts for the durable reflection queue."""
from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio

from server.src.config import Settings
from server.src.memory.episodic.store import EpisodicMemory
from server.src.memory.reflection.worker import ReflectionWorker
from server.src.memory.storage.sqlite import SQLiteDatabase
from server.src.skills.learning.experience import ExperienceLearningService
from server.src.skills.trajectory_store.store import TrajectoryStore


class Cursor:
    def __init__(self, cursor):
        self._cursor = cursor
        self.rowcount = cursor.rowcount

    async def close(self):
        self._cursor.close()

    async def fetchone(self):
        return self._cursor.fetchone()

    async def fetchall(self):
        return self._cursor.fetchall()


class AsyncSQLite:
    def __init__(self, path: Path):
        self.raw = sqlite3.connect(path, check_same_thread=False)
        self.raw.row_factory = sqlite3.Row
        self.raw.execute("PRAGMA foreign_keys=ON")

    async def execute(self, sql, args=()):
        cursor = self.raw.execute(sql, args)
        if "RETURNING" not in sql:
            self.raw.commit()
        return Cursor(cursor)

    async def execute_returning(self, sql, args=()):
        cursor = self.raw.execute(sql, args)
        rows = [dict(row) for row in cursor.fetchall()]
        cursor.close()
        self.raw.commit()
        return rows

    async def executescript(self, sql):
        self.raw.executescript(sql)

    async def commit(self):
        self.raw.commit()

    async def close(self):
        self.raw.close()


class Policy:
    def __init__(self):
        self.enabled = True
        self.settings = {}

    async def get_setting(self, key, default):
        if key == "automatic_memory":
            return self.enabled
        return self.settings.get(key, default)

    async def set_setting(self, key, value):
        self.settings[key] = value


class LLMConfig:
    async def get(self):
        return {"default_model": "ollama/test-model"}


class NoVector:
    def upsert(self, *_args, **_kwargs):
        pass


@pytest_asyncio.fixture
async def engine(tmp_path):
    db = SQLiteDatabase(tmp_path / "memory.db")
    db._conn = AsyncSQLite(db.path)
    await db._migrate()
    store = TrajectoryStore(db)
    learning = ExperienceLearningService(db, store)
    episodic = EpisodicMemory(SimpleNamespace(sqlite=db, vector=NoVector()))
    config = Settings()
    config.memory.reflection.poll_seconds = 0.1
    config.memory.reflection.max_daily_reviews = 10
    policy = Policy()
    calls: list[dict] = []

    async def reviewer(system, user):
        calls.append(json.loads(user))
        return json.dumps({"summary": "A recovery strategy was observed", "insights": [{
            "kind": "lesson", "content": "A failed extraction can be retried using independent sources",
            "confidence": 0.8, "evidence_event_seqs": [calls[-1]["events"][0]["seq"]],
        }]})

    worker = ReflectionWorker(db, store, learning, episodic, config, policy, LLMConfig(), reviewer=reviewer)
    try:
        yield SimpleNamespace(db=db, store=store, learning=learning, episodic=episodic,
                              config=config, policy=policy, worker=worker, calls=calls,
                              reviewer=reviewer)
    finally:
        await worker.stop()
        await db.close()


async def trace(engine, *, user_id="local", workspace=None, error=False):
    _, tid = await engine.store.begin(
        goal="Investigate blocked website and find another source",
        thread_id="thread-1", user_id=user_id,
        metadata={"workspace_path": workspace},
    )
    await engine.store.append(tid, event_type="tool.call.delta", data={
        "name": "web_extract", "password": "very-secret", "args": {"secret": "abc"},
    })
    await engine.store.append(tid, event_type="tool.result", data={
        "name": "web_extract", "status": "error" if error else "ok",
        "content": "sk-proj-secretsecretsecretsecret", "message": "HTTP 403",
    })
    await engine.store.finish(tid, outcome="completed", result="A relevant alternative was found")
    return tid


@pytest.mark.asyncio
async def test_enqueue_review_only_and_provenance(engine):
    tid = await trace(engine, error=True)
    assert await engine.worker.enqueue(tid)
    assert not await engine.worker.enqueue(tid)
    assert await engine.worker.run_once()
    item = (await engine.learning.list())[0]
    assert item["status"] == "needs_review"
    assert item["kind"] == "procedure"
    assert item["evidence"]["source"] == "background_reflection"
    assert item["source_trajectory_id"] == tid
    assert not await engine.learning.context("A failed extraction can be retried")
    result = await engine.worker.status()
    assert result["counts"]["completed"] == 1
    assert result["jobs"][0]["result"]["experience_ids"] == [item["id"]]
    assert "secretsecret" not in json.dumps(engine.calls)
    assert "password" not in json.dumps(engine.calls)


@pytest.mark.asyncio
async def test_restart_recovery_and_retries(engine):
    tid = await trace(engine)
    assert await engine.worker.enqueue(tid)
    await engine.db.execute("""UPDATE reflection_jobs SET status='processing',
                              lease_until='2020-01-01T00:00:00+00:00'""")
    # A different worker instance recovers the lease and processes the same job.
    recovered = ReflectionWorker(
        engine.db, engine.store, engine.learning, engine.episodic,
        engine.config, engine.policy, LLMConfig(), reviewer=engine.reviewer,
    )
    await recovered._recover()
    assert await recovered.run_once()
    assert not await recovered.run_once()
    assert (await recovered.status())["counts"]["completed"] == 1
    assert len(await engine.learning.list()) == 1


@pytest.mark.asyncio
async def test_budget_retry_and_terminal_failure(engine):
    tid = await trace(engine)
    engine.config.memory.reflection.max_attempts = 2
    engine.config.memory.reflection.retry_delay_seconds = 1
    async def broken(_system, _user):
        raise RuntimeError("cloud message contained credential")
    engine.worker._reviewer = broken
    assert await engine.worker.enqueue(tid)
    assert await engine.worker.run_once()
    state = await engine.worker.status()
    assert state["counts"]["pending"] == 1
    assert state["jobs"][0]["last_error"] == "RuntimeError"
    # Force a retry to be due without sleeping.
    await engine.db.execute("UPDATE reflection_jobs SET next_attempt_at='2020-01-01T00:00:00+00:00'")
    assert await engine.worker.run_once()
    assert (await engine.worker.status())["counts"]["failed"] == 1
    assert not await engine.worker.run_once()
    assert await engine.learning.list() == []


@pytest.mark.asyncio
async def test_feedback_is_distinct_review_and_not_fake_success(engine):
    tid = await trace(engine)
    assert await engine.worker.enqueue(tid)
    await engine.worker.run_once()
    await engine.learning.feedback(trajectory_id=tid, rating="failure", note="Please verify sources")
    assert await engine.worker.enqueue(tid, reason="feedback")
    assert await engine.worker.run_once()
    assert len(engine.calls) == 2
    assert engine.calls[0]["outcome_verified"] is False
    assert engine.calls[1]["outcome_verified"] is True
    assert engine.calls[1]["user_feedback"] == "failure"
    assert (await engine.worker.status())["counts"]["completed"] == 2


@pytest.mark.asyncio
async def test_toggle_off_workspace_scope_and_invalid_grounding(engine):
    tid = await trace(engine)
    engine.policy.enabled = False
    assert not await engine.worker.enqueue(tid)
    engine.policy.enabled = True
    scoped_tid = await trace(engine, user_id="private", workspace="/private/project")
    assert await engine.worker.enqueue(scoped_tid)
    assert await engine.worker.run_once()
    assert not await engine.learning.list()  # no global leak of scoped lessons
    assert (await engine.worker.status())["jobs"][0]["result"]["insights"]
    tid2 = await trace(engine)
    async def invented(_sys, _user):
        return {"summary": "unsupported", "insights": [{
            "kind": "procedure", "content": "Run unsupported shell steps every time",
            "confidence": 1.0, "evidence_event_seqs": [999999],
        }]}
    engine.worker._reviewer = invented
    assert await engine.worker.enqueue(tid2)
    assert await engine.worker.run_once()
    assert not await engine.learning.list()


@pytest.mark.asyncio
async def test_skips_trivial_and_interrupted(engine):
    _, tid = await engine.store.begin(goal="Hi", thread_id="t")
    await engine.store.finish(tid, outcome="completed", result="Hello")
    assert not await engine.worker.enqueue(tid)
    _, paused = await engine.store.begin(goal="Run command", thread_id="t")
    await engine.store.append(paused, event_type="tool.call.delta", data={"name": "shell"})
    await engine.store.finish(paused, outcome="interrupted")
    assert not await engine.worker.enqueue(paused)
    assert not await engine.worker.run_once()


@pytest.mark.asyncio
async def test_review_configuration_persists_and_respects_disable(engine):
    config = await engine.worker.get_config()
    assert config["enabled"] is True and config["model"] is None
    updated = await engine.worker.update_config({
        "model": "ollama/local-review", "enabled": False,
        "max_daily_reviews": 3, "timeout_seconds": 10,
    })
    assert updated["model"] == "ollama/local-review"
    assert not engine.worker._cfg.enabled
    assert engine.policy.settings["memory.reflection.settings"] == updated
    tid = await trace(engine)
    assert not await engine.worker.enqueue(tid)

    fresh = ReflectionWorker(
        engine.db, engine.store, engine.learning, engine.episodic,
        Settings(), engine.policy, LLMConfig(), reviewer=engine.reviewer,
    )
    await fresh.load_config()
    assert (await fresh.get_config()) == updated
    assert not await fresh.run_once()
    with pytest.raises(ValueError):
        await fresh.update_config({"max_daily_reviews": -1})
    assert await fresh.get_config() == updated
