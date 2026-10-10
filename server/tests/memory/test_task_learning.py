"""SQLite-only tests for the v22 autonomous multi-run learning pipeline."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from server.src.config import Settings
from server.src.guardrails.policy import PermissionPolicyStore
from server.src.memory.episodic.store import EpisodicMemory
from server.src.memory.learning.worker import ContinuousLearningWorker
from server.src.memory.storage.sqlite import SQLiteDatabase
from server.src.skills.learning.experience import ExperienceLearningService
from server.src.skills.repository import SkillRepository
from server.src.skills.trajectory_store.store import TrajectoryStore


class Cursor:
    def __init__(self, cur):
        self.cur, self.rowcount = cur, cur.rowcount

    async def fetchone(self):
        return self.cur.fetchone()

    async def fetchall(self):
        return self.cur.fetchall()

    async def close(self):
        self.cur.close()


class Connection:
    def __init__(self, path: Path):
        self.raw = sqlite3.connect(path, check_same_thread=False)
        self.raw.row_factory = sqlite3.Row
        self.raw.execute('PRAGMA foreign_keys=ON')

    async def execute(self, query, params=()):
        return Cursor(self.raw.execute(query, params))

    async def executescript(self, query):
        self.raw.executescript(query)

    async def commit(self):
        self.raw.commit()

    async def rollback(self):
        self.raw.rollback()

    async def close(self):
        self.raw.close()


class Vector:
    def __init__(self):
        self.docs = []

    def upsert(self, tier, key, text, payload=None):
        self.docs.append((tier, key, text, payload))


@pytest_asyncio.fixture
async def learning_env(tmp_path):
    db = SQLiteDatabase(tmp_path / 'learning.db')
    db._conn = Connection(db.path)
    await db._migrate()
    traces = TrajectoryStore(db)
    experiences = ExperienceLearningService(db, traces)
    provider = SimpleNamespace(sqlite=db, vector=Vector())
    episodes = EpisodicMemory(provider)
    policy = PermissionPolicyStore(db)
    settings = Settings()
    settings.memory.reflection.max_daily_reviews = 20
    settings.memory.reflection.poll_seconds = 0.1
    llm_settings = SimpleNamespace(get=AsyncMock(return_value={
        'default_model':'test/default','default_provider':'test',
    }))
    repo = SkillRepository(db)
    skills = SimpleNamespace(repository=repo, evaluator=SimpleNamespace(evaluate=AsyncMock()),
                             promoter=SimpleNamespace(promote=AsyncMock()))

    async def reviewer(_system, prompt):
        payload = json.loads(prompt)
        result = next((e for e in payload['events'] if e['event']=='tool.result'), None)
        if result is None:
            return {'summary':'No observed tool result','insights':[]}
        return {'summary':'Observed reusable tool sequence', 'insights':[
            {'kind':'procedure','content':'Search sources and check results before answering future requests',
             'confidence':0.85,'evidence_event_seqs':[result['seq']]},
        ]}

    worker = ContinuousLearningWorker(db,traces,experiences,episodes,settings,policy,llm_settings,
                                      reviewer=reviewer,skills=skills)
    try:
        yield SimpleNamespace(db=db,traces=traces,experiences=experiences,episodes=episodes,
                              worker=worker,skills=skills,provider=provider)
    finally:
        await worker.stop()
        await db.close()


@pytest.mark.asyncio
async def test_migration_and_cross_turn_task_episode(learning_env):
    env = learning_env
    assert (await env.db.fetchone('SELECT MAX(version) AS v FROM schema_version'))['v'] == 22
    first_task, first = await env.traces.begin(goal='Research university websites and verify results',thread_id='chat-1')
    await env.traces.append(first,event_type='user.task',data={'content':'Research university websites and verify results'})
    await env.traces.append(first,event_type='tool.result',data={'name':'web_search','status':'error','error':'403'})
    await env.traces.finish(first,outcome='completed',result='Search blocked')
    second_task, second = await env.traces.begin(goal='Continue researching university websites and verify results',thread_id='chat-2')
    assert second_task == first_task
    await env.traces.append(second,event_type='user.task',data={'content':'Continue researching university websites and verify results'})
    await env.traces.append(second,event_type='tool.call.delta',data={'name':'web_search'})
    await env.traces.append(second,event_type='tool.result',data={'name':'web_search','status':'ok'})
    await env.traces.finish(second,outcome='completed',result='Alternatives found')
    assert await env.worker.enqueue(second)
    assert await env.worker.run_once()
    assert (await env.db.fetchone('SELECT COUNT(*) AS n FROM task_learning_jobs'))['n'] == 1
    row=await env.db.fetchone('SELECT * FROM episodes WHERE logical_task_id=?',(first_task,))
    assert row and 'Observed runs: 2' in row['summary']
    assert not row['outcome_verified']
    assert len(json.loads(row['evidence'])['trajectory_ids'])==2
    experience=await env.experiences.list(status='active')
    assert experience and experience[0]['kind']=='procedure'
    candidates=await env.skills.repository.list_candidates()
    assert len(candidates)==1 and candidates[0]['status']=='candidate'
    env.skills.evaluator.evaluate.assert_not_called()  # no independent graded holdouts
    assert not await env.worker.enqueue(second)  # same event watermark, deduplicated


@pytest.mark.asyncio
async def test_scope_and_topic_isolation(learning_env):
    store=learning_env.traces
    task1,_=await store.begin(goal='Audit source links',thread_id='a',metadata={'workspace_path':'/a'})
    task2,_=await store.begin(goal='Continue audit source links',thread_id='b',metadata={'workspace_path':'/b'})
    task3,_=await store.begin(goal='New task: write a recipe',thread_id='a',metadata={'workspace_path':'/a'})
    assert len({task1,task2,task3})==3


@pytest.mark.asyncio
async def test_user_corrections_are_active_without_approval(learning_env):
    item=await learning_env.experiences.observe_user('No, use Ollama for the model instead')
    assert item and item['status']=='active'
    assert 'Ollama' in await learning_env.experiences.context('Ollama for the model')


@pytest.mark.asyncio
async def test_crash_recovery_and_settings(learning_env):
    env=learning_env
    task,tid=await env.traces.begin(goal='Inspect a document',thread_id='c')
    await env.traces.append(tid,event_type='tool.result',data={'name':'read_file','status':'ok'})
    await env.traces.finish(tid,outcome='completed',result='Inspected')
    # Simulate crash after a run completed but before enqueue.
    await env.worker.start()
    await env.worker.stop()
    jobs=await env.db.fetchone('SELECT COUNT(*) AS n FROM task_learning_jobs')
    assert jobs['n']==1
    config=await env.worker.update_config({'enabled':False})
    assert not config['enabled']
    assert not await env.worker.enqueue(tid)
    assert not await env.worker.run_once()
