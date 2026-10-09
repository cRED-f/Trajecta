"""Offline contracts for evidence-driven procedural proposals and candidate gate."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio

from server.src.memory.storage.sqlite import SQLiteDatabase
from server.src.skills.learning.procedures import ProcedureRefinementService
from server.src.skills.repository import SkillRepository
from server.src.skills.trajectory_store.store import TrajectoryStore
from server.src.skills.representation.skill import Skill, SkillWorkflow, WorkflowStep


class Cursor:
    def __init__(self, inner):
        self._inner = inner
        self.rowcount = inner.rowcount

    async def fetchone(self):
        return self._inner.fetchone()

    async def fetchall(self):
        return self._inner.fetchall()

    async def close(self):
        self._inner.close()


class Connection:
    def __init__(self, path):
        self.raw = sqlite3.connect(path)
        self.raw.row_factory = sqlite3.Row
        self.raw.execute("PRAGMA foreign_keys=ON")

    async def execute(self, sql, args=()):
        cur = self.raw.execute(sql, args)
        return Cursor(cur)

    async def executescript(self, sql):
        self.raw.executescript(sql)

    async def commit(self):
        self.raw.commit()

    async def close(self):
        self.raw.close()


@pytest_asyncio.fixture
async def env(tmp_path):
    db = SQLiteDatabase(tmp_path / "procedures.db")
    db._conn = Connection(db.path)
    await db._migrate()
    trajectories = TrajectoryStore(db)
    repository = SkillRepository(db)
    service = ProcedureRefinementService(db, trajectories, repository)
    try:
        yield SimpleNamespace(db=db, trajectories=trajectories, repository=repository,
                              service=service)
    finally:
        await db.close()


async def trace(env, *, user_id="local", workspace=None, goal="Investigate website extraction issues",
                tools=("web_search", "read_file"), status="ok", result="Task response"):
    _, tid = await env.trajectories.begin(goal=goal, thread_id="thread", user_id=user_id,
                                          metadata={"workspace_path": workspace})
    for index, tool in enumerate(tools):
        call_id = f"call-{index}"
        await env.trajectories.append(tid, event_type="tool.call.delta", data={
            "name": tool, "id": call_id,
            "args": {"password": "sk-proj-secretsecretsecretsecret"},
        })
        await env.trajectories.append(tid, event_type="tool.call.delta", data={
            "name": None, "args": "continuation secret"})
        await env.trajectories.append(tid, event_type="tool.result", data={
            "name": tool, "tool_call_id": call_id, "status": status,
            "content": "sk-proj-secretsecretsecretsecret", "message": "do unsafe thing",
        })
    await env.trajectories.finish(tid, outcome="completed", result=result)
    return tid


def insight(trace, content="Look for another source when document retrieval cannot complete"):
    return [{"kind": "procedure", "content": content,
             "evidence_event_seqs": [trace["steps"][0]["seq"]]}]


@pytest.mark.asyncio
async def test_reflection_draft_has_ordered_evidence_no_arguments(env):
    tid = await trace(env)
    data = await env.trajectories.get_with_task(tid)
    draft = await env.service.propose(tid, trigger="reflection", insights=insight(data))
    assert draft["status"] == "needs_review"
    assert draft["kind"] == "new"
    assert [s["tool"] for s in draft["steps"]] == ["web_search", "read_file"]
    assert all(s["outcome"] == "observed_result" for s in draft["steps"])
    assert len(draft["evidence"]["source_event_seqs"]) == 4
    assert not draft["evidence"]["raw_arguments_stored"]
    assert "secretsecret" not in json.dumps(draft)
    assert (await env.service.propose(tid, trigger="reflection", insights=insight(data)))["id"] == draft["id"]
    assert len(await env.service.list()) == 1


@pytest.mark.asyncio
async def test_invalid_refs_or_only_tool_calls_do_not_create_drafts(env):
    tid = await trace(env, tools=())
    assert await env.service.propose(tid, trigger="reflection", insights=[{
        "kind": "procedure", "content": "Useful valid description longer than minimum",
        "evidence_event_seqs": [999],
    }]) is None
    tid2 = await trace(env)
    assert await env.service.propose(tid2, trigger="reflection", insights=[{
        "kind": "procedure", "content": "Useful valid description longer than minimum",
        "evidence_event_seqs": [999],
    }]) is None


@pytest.mark.asyncio
async def test_explicit_feedback_gates_candidate_and_no_auto_promotion(env):
    tid = await trace(env)
    await env.trajectories.finish(tid, outcome="success", metadata={"user_feedback": "success"})
    draft = await env.service.propose(tid, trigger="feedback")
    assert draft["user_confirmed"]
    with pytest.raises(ValueError, match="approve"):
        await env.service.create_candidate(draft["id"], name="web-research")
    await env.service.review(draft["id"], decision="approve")
    with pytest.raises(ValueError, match="explicit name"):
        await env.service.create_candidate(draft["id"])
    candidate = await env.service.create_candidate(draft["id"], name="web-research")
    assert candidate["status"] == "candidate"
    assert candidate["metadata"]["skill_bundle"]["metadata"]["notes"]["requires_manual_evaluation"]
    assert not await env.repository.list_active()
    assert (await env.service.get(draft["id"]))["status"] == "candidate_created"


@pytest.mark.asyncio
async def test_failed_procedure_is_not_eligible_for_candidate(env):
    tid = await trace(env, status="error")
    await env.trajectories.finish(tid, outcome="failure", metadata={"user_feedback": "failure",
                                                               "user_feedback_note": "HTTP 403"})
    draft = await env.service.propose(tid, trigger="feedback")
    assert not draft["user_confirmed"]
    assert all(step["outcome"] == "error" for step in draft["steps"])
    await env.service.review(draft["id"], decision="approve")
    with pytest.raises(ValueError, match="positive feedback"):
        await env.service.create_candidate(draft["id"], name="bad-procedure")


@pytest.mark.asyncio
async def test_revisions_record_snapshots_and_rejection_is_final(env):
    tid = await trace(env)
    data = await env.trajectories.get_with_task(tid)
    draft = await env.service.propose(tid, trigger="reflection", insights=insight(data))
    changed = await env.service.propose(tid, trigger="reflection", insights=insight(
        data, "Use alternative independent documentation sources when extraction is blocked"))
    assert changed["id"] == draft["id"] and changed["version"] == 2
    history = await env.service.history(draft["id"])
    assert len(history) == 1 and history[0]["version"] == 1
    await env.service.review(draft["id"], decision="reject")
    unchanged = await env.service.propose(tid, trigger="reflection", insights=insight(data))
    assert unchanged["status"] == "rejected" and unchanged["version"] == 2
    with pytest.raises(ValueError):
        await env.service.review(draft["id"], decision="approve")


@pytest.mark.asyncio
async def test_workspace_isolation_and_reflection_to_feedback_link(env):
    tid = await trace(env, workspace="/private/work", user_id="another-user")
    data = await env.trajectories.get_with_task(tid)
    draft = await env.service.propose(tid, trigger="reflection", insights=insight(data))
    assert draft["scope"].startswith("workspace:") and draft["user_id"] == "another-user"
    assert draft["target_skill_name"] is None
    await env.trajectories.finish(tid, outcome="success", metadata={"user_feedback": "success"})
    feedback = await env.service.propose(tid, trigger="feedback")
    assert feedback["parent_id"] == draft["id"]
    await env.service.review(feedback["id"], decision="approve")
    with pytest.raises(ValueError, match="scoped proposals"):
        await env.service.create_candidate(feedback["id"], name="private-skill")


@pytest.mark.asyncio
async def test_matching_existing_skill_creates_manual_revision_candidate(env):
    base = Skill(name="website-research", description="Investigate website extraction issues",
                 instructions="Original instructions remain unchanged.",
                 workflow=SkillWorkflow(trigger="Investigate website extraction issues",
                                        steps=[WorkflowStep(instruction="Search for sources")]))
    snap = await env.repository.add_version(base, source_candidate_id=None,
                                            source_evaluation_id=None, status="active")
    await env.repository.set_active(base, version_id=snap["id"])
    tid = await trace(env, goal="Investigate website extraction issues")
    await env.trajectories.finish(tid, outcome="success", metadata={"user_feedback": "success"})
    draft = await env.service.propose(tid, trigger="feedback")
    assert draft["kind"] == "revision"
    assert draft["target_skill_name"] == "website-research"
    assert draft["base_skill_version"] == "0.1.0"
    await env.service.review(draft["id"], decision="approve")
    candidate = await env.service.create_candidate(draft["id"])
    assert candidate["name"] == "website-research"
    skill = await env.repository.get_candidate_skill(candidate["id"])
    assert "Original instructions remain unchanged." in skill.instructions
    assert "Proposed change" in skill.instructions
    assert (await env.repository.get_active("website-research"))["version"] == "0.1.0"


@pytest.mark.asyncio
async def test_target_version_change_blocks_revision_candidate(env):
    base = Skill(name="website-research", description="Investigate website extraction issues",
                 instructions="Original", workflow=SkillWorkflow(trigger="research"))
    snap = await env.repository.add_version(base, source_candidate_id=None,
                                            source_evaluation_id=None, status="active")
    await env.repository.set_active(base, version_id=snap["id"])
    tid = await trace(env, goal="Investigate website extraction issues")
    await env.trajectories.finish(tid, outcome="success", metadata={"user_feedback": "success"})
    draft = await env.service.propose(tid, trigger="feedback")
    assert draft["kind"] == "revision"
    await env.service.review(draft["id"], decision="approve")
    await env.db.execute("UPDATE skills SET version='0.2.0' WHERE name='website-research'")
    with pytest.raises(ValueError, match="target skill has changed"):
        await env.service.create_candidate(draft["id"])


@pytest.mark.asyncio
async def test_reflection_worker_creates_evidence_linked_proposal(env):
    from server.src.config import Settings
    from server.src.memory.episodic.store import EpisodicMemory
    from server.src.memory.reflection.worker import ReflectionWorker
    from server.src.skills.learning.experience import ExperienceLearningService

    class Policy:
        async def get_setting(self, key, default):
            return True

    class LLMSettings:
        async def get(self):
            return {"default_model": "ollama/test-model", "default_provider": "ollama"}

    class Vector:
        def upsert(self, *_args, **_kwargs):
            return None

    async def reviewer(_system, prompt):
        event_id = json.loads(prompt)["events"][0]["seq"]
        return {"summary": "Observed a retrieval workflow", "insights": [{
            "kind": "procedure", "content": "Search and then inspect a relevant document before responding",
            "confidence": 0.9, "evidence_event_seqs": [event_id],
        }]}

    episode = EpisodicMemory(SimpleNamespace(sqlite=env.db, vector=Vector()))
    worker = ReflectionWorker(
        env.db, env.trajectories, ExperienceLearningService(env.db, env.trajectories),
        episode, Settings(), Policy(), LLMSettings(), reviewer=reviewer,
        procedures=env.service,
    )
    tid = await trace(env)
    assert await worker.enqueue(tid)
    assert await worker.run_once()
    jobs = await worker.status()
    draft_id = jobs["jobs"][0]["result"]["procedure_draft_id"]
    draft = await env.service.get(draft_id)
    assert draft["status"] == "needs_review"
    assert draft["source_trajectory_id"] == tid
    assert not await env.repository.list_active()


@pytest.mark.asyncio
async def test_api_workspace_scope_and_review(env):
    # Load the route in isolation. The full router package imports the
    # application-only Deep Agents backend, absent in this offline test.
    import importlib.util
    import sys
    route_path = Path(__file__).parents[2] / "src/api/routes/learning.py"
    spec = importlib.util.spec_from_file_location("test_learning_routes", route_path)
    routes = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = routes
    spec.loader.exec_module(routes)
    from fastapi import HTTPException

    req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(procedure_refinement=env.service)))
    tid = await trace(env, workspace="/my/work")
    await env.trajectories.finish(tid, outcome="success", metadata={"user_feedback": "success"})
    draft = await env.service.propose(tid, trigger="feedback")
    assert not (await routes.list_procedures(req))["items"]
    assert len((await routes.list_procedures(req, workspace_path="/my/work"))["items"]) == 1
    with pytest.raises(HTTPException) as exc:
        await routes.get_procedure(draft["id"], req)
    assert exc.value.status_code == 404
    result = await routes.review_procedure(draft["id"], routes.ProcedureReviewRequest(decision="reject"),
                                    req, workspace_path="/my/work")
    assert result["status"] == "rejected"
