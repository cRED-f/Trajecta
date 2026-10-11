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


@pytest.mark.asyncio
async def test_reflection_model_and_budget_survive_new_worker_instance(learning_env):
    """Configuration is stored in SQLite, not just in a mounted UI component."""
    env = learning_env
    expected = await env.worker.update_config({
        'enabled': False, 'model': '9router/meta-llama/llama-3.3-70b',
        'max_daily_reviews': 8, 'max_output_tokens': 850, 'timeout_seconds': 60,
    })
    restored_worker = ContinuousLearningWorker(
        env.db, env.traces, env.experiences, env.episodes, Settings(),
        PermissionPolicyStore(env.db), env.worker.llm_settings,
    )
    assert await restored_worker.get_config() == expected
    assert expected['model'] == '9router/meta-llama/llama-3.3-70b'


@pytest.mark.asyncio
async def test_file_outcome_requires_user_confirmation_and_complete_snapshot(learning_env):
    """A model's completion and a tool success cannot independently verify work."""
    env = learning_env
    from server.src.skills.representation.skill import OutcomeAssertion, OutcomeAssertionType
    assertion = OutcomeAssertion(type=OutcomeAssertionType.FILE_SHA256,
                                 path="/workspace/report.md", expected_sha256="a" * 64)
    evidence = {
        "outcome_capture_complete": True,
        "replay_fixture_complete": True,
        "replay_fixture_id": "trusted-fixture",
        "outcome_assertions": [assertion.model_dump(mode="json")],
    }
    _, unconfirmed = await env.traces.begin(goal="New task: Write an evaluation report", thread_id="u")
    await env.traces.append(unconfirmed, event_type="tool.result",
                            data={"name": "write_file", "status": "success"})
    await env.traces.finish(unconfirmed, outcome="completed", result="Finished", metadata=evidence)
    saved = await env.episodes.consolidate(unconfirmed)
    assert saved is not None and saved["outcome_verified"] is False

    _, confirmed = await env.traces.begin(goal="New task: Write an evaluation report for a client",
                                          thread_id="v")
    await env.traces.append(confirmed, event_type="tool.result",
                            data={"name": "write_file", "status": "success"})
    await env.traces.finish(confirmed, outcome="success", result="Finished",
                             metadata={**evidence, "user_feedback": "success"})
    verified = await env.episodes.consolidate(confirmed)
    assert verified is not None and verified["outcome_verified"] is True
    assert verified["evidence"]["replay_fixture_id"] == "trusted-fixture"
    assert verified["evidence"]["verification_trajectory_id"] == confirmed
    assert verified["evidence"]["outcome_assertions"] == evidence["outcome_assertions"]


@pytest.mark.asyncio
async def test_existing_skill_rechecks_verified_heldouts_and_deduplicates_evaluation(learning_env):
    """Later verified outcomes unlock previous candidates; no unrelated task leaks."""
    env = learning_env
    from server.src.memory.learning.skills import AutonomousSkillDiscovery
    from server.src.skills.representation.skill import OutcomeAssertion, OutcomeAssertionType
    finder = AutonomousSkillDiscovery(env.db, env.skills)
    original, tid = await env.traces.begin(goal="Generate structured architecture diagram documentation",
                                            thread_id="original")
    await env.traces.append(tid, event_type="tool.call.delta", data={"name": "write_file"})
    await env.traces.append(tid, event_type="tool.result", data={"name": "write_file", "status": "ok"})
    await env.traces.finish(tid, outcome="completed", result="Draft created")
    trace = await env.traces.get_with_task(tid)
    seq = next(event["seq"] for event in trace["steps"] if event["type"] == "tool.result")
    insight = SimpleNamespace(evidence_event_seqs=[seq],
             content="A reusable architecture document workflow should create and check its output")
    candidate_id = await finder.discover(original, [trace], insight)
    assert candidate_id is not None
    initial = await env.skills.repository.get_candidate(candidate_id)
    assert initial["metadata"]["evaluation_blocker"] == "awaiting_verified_cases"
    assert initial["metadata"]["verified_heldout_cases"] == 0

    assertion = OutcomeAssertion(type=OutcomeAssertionType.FILE_SHA256,
                                 path="/workspace/architecture.md", expected_sha256="b" * 64)
    for index in (1, 2):
        _, other_id = await env.traces.begin(
            goal="New task: Generate structured architecture diagram documentation",
            thread_id=f"heldout-{index}")
        await env.traces.append(other_id, event_type="tool.result",
                                data={"name": "write_file", "status": "ok"})
        await env.traces.finish(other_id, outcome="success", result="Created",
           metadata={"user_feedback": "success", "outcome_capture_complete": True,
                     "replay_fixture_complete": True,
                     "replay_fixture_id": f"fixture-{index}",
                     "outcome_assertions": [assertion.model_dump(mode="json")]})
        await env.episodes.consolidate(other_id)

    # A completed evaluation should use each distinct historical task once.
    env.skills.evaluator.evaluate.return_value = SimpleNamespace(verdict="pass", id="evaluation-pass")
    await finder.refresh_pending()
    env.skills.evaluator.evaluate.assert_awaited_once_with(candidate_id)
    env.skills.promoter.promote.assert_awaited_once_with(
        candidate_id=candidate_id, evaluation_id="evaluation-pass")
    updated = await env.skills.repository.get_candidate_skill(candidate_id)
    assert len(updated.eval_cases) == 2
    assert all(case.metadata["requires_replay_fixture"] for case in updated.eval_cases)
    assert len({case.source_trajectory_id for case in updated.eval_cases}) == 2
    await finder.refresh_pending()
    assert env.skills.evaluator.evaluate.await_count == 1


@pytest.mark.asyncio
async def test_sensitive_candidate_never_auto_evaluates(learning_env):
    env = learning_env
    from server.src.memory.learning.skills import AutonomousSkillDiscovery
    finder = AutonomousSkillDiscovery(env.db, env.skills)
    task_id, tid = await env.traces.begin(goal="Send external messages on behalf of user",
                                          thread_id="external")
    await env.traces.append(tid, event_type="tool.result",
                            data={"name": "http_request", "status": "ok"})
    await env.traces.finish(tid, outcome="completed", result="Sent")
    trace = await env.traces.get_with_task(tid)
    seq = next(event["seq"] for event in trace["steps"] if event["type"] == "tool.result")
    candidate = await finder.discover(task_id, [trace], SimpleNamespace(
        content="Send external requests only after obtaining explicit permissions",
        evidence_event_seqs=[seq]))
    assert candidate is not None
    record = await env.skills.repository.get_candidate(candidate)
    assert record["metadata"]["evaluation_blocker"] == "sensitive_tools_require_manual_evidence"
    env.skills.evaluator.evaluate.assert_not_called()
    env.skills.promoter.promote.assert_not_called()


@pytest.mark.asyncio
async def test_replay_fixture_uses_selected_workspace_and_never_guesses_initial_state(
    learning_env, tmp_path,
):
    from server.src.skills.evaluation.fixtures import ReplayFixtureStore
    env = learning_env
    default = tmp_path / "default"
    selected = tmp_path / "selected"
    default.mkdir()
    selected.mkdir()
    (default / "not-selected.txt").write_text("untouched")
    (selected / "report.md").write_text("before")
    settings = Settings()
    settings.tools.workspace_root = str(default)
    settings.skills.fixtures.root = str(tmp_path / "fixtures")
    settings.skills.fixtures.enabled = True
    settings.skills.fixtures.capture_workspace = True
    fixture_store = ReplayFixtureStore(settings, env.db)
    _, tid = await env.traces.begin(goal="Edit a report", thread_id="selected")
    fixture = await fixture_store.capture_initial_state(
        trajectory_id=tid, metadata={"workspace_path": str(selected)},
    )
    assert fixture and fixture.complete
    assert fixture.metadata["captured_workspace_path"] == str(selected)
    assert [f.virtual_path for f in fixture.files] == ["/workspace/report.md"]
    (selected / "report.md").write_text("after")
    assertions = await fixture_store.capture_outcome(tid)
    assert any(assertion.path == "/workspace/report.md" for assertion in assertions)
    assert not any(assertion.path == "/workspace/not-selected.txt" for assertion in assertions)

    # A snapshot that omitted the workspace cannot prove changes later.
    settings.skills.fixtures.capture_workspace = False
    incomplete_store = ReplayFixtureStore(settings, env.db)
    _, other = await env.traces.begin(goal="Write another report", thread_id="no-snapshot")
    await incomplete_store.capture_initial_state(trajectory_id=other)
    assert await incomplete_store.capture_outcome(other) == []
    trace = await env.traces.get(other)
    assert trace["metadata"]["outcome_capture_complete"] is False
