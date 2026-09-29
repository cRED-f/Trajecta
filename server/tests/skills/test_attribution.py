"""Live-run attribution: turning a finished trajectory into metrics rows.

Skills are never injected into a run, so the only execution signal is the
agent reading one with ``skill_view``. These tests pin that signal down:
which runs count, how the streamed args are reassembled, and what lands in
``skill_execution_metrics``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from server.src.chat.models import ChatEvent, ConversationCreate, SendMessageRequest
from server.src.chat.repository import ChatRepository
from server.src.chat.runs import ChatRunRegistry
from server.src.chat.runtime import PreparedAgentRun
from server.src.chat.service import ChatService
from server.src.config import Settings
from server.src.memory.storage.sqlite import SQLiteDatabase
from server.src.skills.analytics import SkillExecutionAttributor, SkillMetricsCollector
from server.src.skills.evaluation.fixtures import ReplayFixtureStore
from server.src.skills.repository import SkillRepository
from server.src.skills.representation.skill import Skill, SkillWorkflow
from server.src.skills.trajectory_store import TrajectoryStore


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


async def _db(tmp_path: Any) -> SQLiteDatabase:
    db = SQLiteDatabase(tmp_path / "attribution.db")
    await db.open()
    return db


def _attributor(db: SQLiteDatabase) -> SkillExecutionAttributor:
    return SkillExecutionAttributor(
        trajectories=TrajectoryStore(db),
        repository=SkillRepository(db),
        metrics=SkillMetricsCollector(db),
    )


async def _register(db: SQLiteDatabase, name: str, version: str) -> None:
    """Make ``name`` resolve to ``version`` through get_active()."""

    await SkillRepository(db).set_active(
        Skill(
            name=name,
            description=f"{name} demo",
            instructions="Do the demo thing.",
            version=version,
            workflow=SkillWorkflow(trigger=f"when {name} is needed"),
        ),
        version_id=f"ver-{version}",
    )


async def _trajectory(
    store: TrajectoryStore,
    steps: list[dict[str, Any]],
    *,
    outcome: str | None = "success",
) -> str:
    """Persist a trajectory made of raw event steps, then score it."""

    _, trajectory_id = await store.begin(goal="demo goal", thread_id="thread-1")

    for step in steps:
        await store.append(
            trajectory_id,
            event_type=step["type"],
            data=step.get("data") or {},
            source=step.get("source", "main"),
        )

    if outcome is not None:
        await store.finish(trajectory_id, outcome=outcome, result="done")

    return trajectory_id


def _view(name: str, *, call_id: str = "call-1") -> list[dict[str, Any]]:
    """One complete skill_view call with its result."""

    return [
        {
            "type": "tool.call.delta",
            "data": {"id": call_id, "name": "skill_view", "args": f'{{"name": "{name}"}}'},
        },
        {
            "type": "tool.result",
            "data": {
                "name": "skill_view",
                "tool_call_id": call_id,
                "content": "skill instructions",
                "status": None,
            },
        },
    ]


def _failure() -> dict[str, Any]:
    return {
        "type": "tool.result",
        "data": {
            "name": "web_search",
            "tool_call_id": "call-x",
            "content": "boom",
            "status": "error",
        },
    }


async def _rows(db: SQLiteDatabase) -> list[dict[str, Any]]:
    return await db.fetch("SELECT * FROM skill_execution_metrics ORDER BY skill_name")


# --------------------------------------------------------------------------
# what counts as an execution
# --------------------------------------------------------------------------


async def test_records_one_row_with_version_success_and_latency(tmp_path: Any) -> None:
    db = await _db(tmp_path)

    try:
        await _register(db, "demo-skill", "1.2.0")
        store = TrajectoryStore(db)
        trajectory_id = await _trajectory(store, _view("demo-skill"))

        recorded = await _attributor(db).attribute(trajectory_id)

        assert recorded == ["demo-skill"]
        rows = await _rows(db)
        assert len(rows) == 1
        row = rows[0]
        assert row["skill_name"] == "demo-skill"
        assert row["skill_version"] == "1.2.0"
        assert row["trajectory_id"] == trajectory_id
        assert row["success"] == 1
        assert row["latency_ms"] > 0
        assert row["tool_failures"] == 0
    finally:
        await db.close()


async def test_args_split_across_chunks_without_id_still_resolve(tmp_path: Any) -> None:
    db = await _db(tmp_path)

    try:
        # Only the first chunk carries an id and the tool name, exactly as the
        # model streams them; the rest arrive as bare arg fragments.
        steps = [
            {
                "type": "tool.call.delta",
                "data": {"id": "call-1", "name": "skill_view", "args": '{"na'},
            },
            {"type": "tool.call.delta", "data": {"args": 'me": "demo-skill"}'}},
            {
                "type": "tool.result",
                "data": {"name": "skill_view", "tool_call_id": "call-1", "content": "body"},
            },
        ]
        trajectory_id = await _trajectory(TrajectoryStore(db), steps)

        assert await _attributor(db).attribute(trajectory_id) == ["demo-skill"]
        assert len(await _rows(db)) == 1
    finally:
        await db.close()


async def test_parallel_tool_calls_keep_their_own_names(tmp_path: Any) -> None:
    db = await _db(tmp_path)

    try:
        # LangChain can emit both first chunks in one token. Merging them
        # would mangle both arg strings and lose the skill name entirely.
        steps = [
            {
                "type": "tool.call.delta",
                "data": {"id": "call-1", "name": "web_search", "args": '{"query": "x"}'},
            },
            *_view("demo-skill", call_id="call-2"),
            {
                "type": "tool.result",
                "data": {"name": "web_search", "tool_call_id": "call-1", "content": "hits"},
            },
        ]
        trajectory_id = await _trajectory(TrajectoryStore(db), steps)

        assert await _attributor(db).attribute(trajectory_id) == ["demo-skill"]
        assert len(await _rows(db)) == 1
    finally:
        await db.close()


async def test_repeated_views_of_one_skill_record_a_single_row(tmp_path: Any) -> None:
    db = await _db(tmp_path)

    try:
        steps = _view("demo-skill") + _view("demo-skill", call_id="call-2")
        trajectory_id = await _trajectory(TrajectoryStore(db), steps)

        assert await _attributor(db).attribute(trajectory_id) == ["demo-skill"]
        assert len(await _rows(db)) == 1
    finally:
        await db.close()


async def test_two_skills_in_one_run_record_two_rows(tmp_path: Any) -> None:
    db = await _db(tmp_path)

    try:
        steps = _view("alpha-skill") + _view("beta-skill", call_id="call-2")
        trajectory_id = await _trajectory(TrajectoryStore(db), steps)

        recorded = await _attributor(db).attribute(trajectory_id)

        assert sorted(recorded) == ["alpha-skill", "beta-skill"]
        assert [row["skill_name"] for row in await _rows(db)] == ["alpha-skill", "beta-skill"]
    finally:
        await db.close()


async def test_failure_outcome_records_success_zero(tmp_path: Any) -> None:
    db = await _db(tmp_path)

    try:
        trajectory_id = await _trajectory(
            TrajectoryStore(db), _view("demo-skill"), outcome="failure"
        )

        await _attributor(db).attribute(trajectory_id)

        rows = await _rows(db)
        assert len(rows) == 1
        assert rows[0]["success"] == 0
    finally:
        await db.close()


async def test_unscored_outcomes_record_nothing(tmp_path: Any) -> None:
    db = await _db(tmp_path)

    try:
        store = TrajectoryStore(db)
        attributor = _attributor(db)

        # An approval pause and a user cancel are not scored executions, and
        # neither is a run that has not finished yet (NULL outcome).
        for outcome in ("interrupted", "cancelled", None):
            trajectory_id = await _trajectory(store, _view("demo-skill"), outcome=outcome)

            assert await attributor.attribute(trajectory_id) == []

        assert await _rows(db) == []
    finally:
        await db.close()


# --------------------------------------------------------------------------
# tool failure attribution
# --------------------------------------------------------------------------


async def test_failures_before_the_skill_loaded_are_not_counted(tmp_path: Any) -> None:
    db = await _db(tmp_path)

    try:
        trajectory_id = await _trajectory(
            TrajectoryStore(db), [_failure(), *_view("demo-skill")]
        )

        await _attributor(db).attribute(trajectory_id)

        assert (await _rows(db))[0]["tool_failures"] == 0
    finally:
        await db.close()


async def test_failures_after_the_skill_loaded_are_counted(tmp_path: Any) -> None:
    db = await _db(tmp_path)

    try:
        trajectory_id = await _trajectory(
            TrajectoryStore(db), [*_view("demo-skill"), _failure(), _failure()]
        )

        await _attributor(db).attribute(trajectory_id)

        assert (await _rows(db))[0]["tool_failures"] == 2
    finally:
        await db.close()


# --------------------------------------------------------------------------
# degenerate inputs
# --------------------------------------------------------------------------


async def test_unregistered_skill_records_unknown_version(tmp_path: Any) -> None:
    db = await _db(tmp_path)

    try:
        trajectory_id = await _trajectory(TrajectoryStore(db), _view("ghost-skill"))

        await _attributor(db).attribute(trajectory_id)

        assert (await _rows(db))[0]["skill_version"] == "unknown"
    finally:
        await db.close()


async def test_unparsable_args_are_skipped_without_raising(tmp_path: Any) -> None:
    db = await _db(tmp_path)

    try:
        steps = [
            {
                "type": "tool.call.delta",
                "data": {"id": "call-1", "name": "skill_view", "args": "{not json"},
            },
            {
                "type": "tool.result",
                "data": {"name": "skill_view", "tool_call_id": "call-1", "content": "body"},
            },
        ]
        trajectory_id = await _trajectory(TrajectoryStore(db), steps)

        assert await _attributor(db).attribute(trajectory_id) == []
        assert await _rows(db) == []
    finally:
        await db.close()


async def test_a_failed_skill_view_is_not_an_execution(tmp_path: Any) -> None:
    db = await _db(tmp_path)

    try:
        steps = [
            {
                "type": "tool.call.delta",
                "data": {"id": "call-1", "name": "skill_view", "args": '{"name": "demo-skill"}'},
            },
            {
                "type": "tool.result",
                "data": {
                    "name": "skill_view",
                    "tool_call_id": "call-1",
                    "content": "boom",
                    "status": "error",
                },
            },
        ]
        trajectory_id = await _trajectory(TrajectoryStore(db), steps)

        assert await _attributor(db).attribute(trajectory_id) == []
        assert await _rows(db) == []
    finally:
        await db.close()


async def test_run_without_a_skill_view_records_nothing(tmp_path: Any) -> None:
    db = await _db(tmp_path)

    try:
        steps = [
            _failure(),
            {"type": "run.finished", "data": {"checkpoint_id": "ckpt-1"}},
        ]
        trajectory_id = await _trajectory(TrajectoryStore(db), steps)

        assert await _attributor(db).attribute(trajectory_id) == []
        assert await _rows(db) == []
    finally:
        await db.close()


async def test_unknown_trajectory_id_records_nothing(tmp_path: Any) -> None:
    db = await _db(tmp_path)

    try:
        assert await _attributor(db).attribute("no-such-trajectory") == []
    finally:
        await db.close()


async def test_run_finished_tokens_are_recorded(tmp_path: Any) -> None:
    db = await _db(tmp_path)

    try:
        steps = [
            *_view("demo-skill"),
            {
                "type": "run.finished",
                "data": {
                    "checkpoint_id": "ckpt-1",
                    "tokens": {"input": 30, "output": 12, "total": 42},
                },
            },
        ]
        trajectory_id = await _trajectory(TrajectoryStore(db), steps)

        await _attributor(db).attribute(trajectory_id)

        assert (await _rows(db))[0]["tokens_used"] == 42
    finally:
        await db.close()


# --------------------------------------------------------------------------
# integration: a real chat stream ends in a metrics row
# --------------------------------------------------------------------------


class DummyAttachments:
    async def save(self, **_: Any) -> Any:
        raise AssertionError("not used")


class DummyRag:
    pass


class SkillRunRuntime:
    """Streams one skill_view call (or none), then finishes with usage."""

    def __init__(self, *, skill_name: str | None = "demo-skill") -> None:
        self._skill_name = skill_name

    async def prepare(
        self,
        *,
        conversation: Any,
        thread_id: str,
        model_name: str | None,
        base_checkpoint_id: str | None,
    ) -> PreparedAgentRun:
        return PreparedAgentRun(
            agent=None,
            config={"configurable": {"thread_id": thread_id}},
            model_name=model_name or conversation.model,
            mcp_tool_count=0,
            thread_id=thread_id,
        )

    async def stream_prepared(
        self,
        *,
        prepared: Any,
        conversation: Any,
        user_content: str,
        attachments: list[Any],
        cancel_event: Any,
    ) -> AsyncIterator[ChatEvent]:
        if self._skill_name is not None:
            yield ChatEvent(
                type="tool.call.delta",
                conversation_id=conversation.id,
                run_id="internal",
                data={"source": "main", "name": "skill_view", "id": "call-1", "args": '{"name": "'},
            )
            yield ChatEvent(
                type="tool.call.delta",
                conversation_id=conversation.id,
                run_id="internal",
                data={"source": "main", "args": f'{self._skill_name}"}}'},
            )
            yield ChatEvent(
                type="tool.result",
                conversation_id=conversation.id,
                run_id="internal",
                data={
                    "source": "main",
                    "name": "skill_view",
                    "tool_call_id": "call-1",
                    "content": "skill instructions",
                    "status": None,
                },
            )
        yield ChatEvent(
            type="message.delta",
            conversation_id=conversation.id,
            run_id="internal",
            data={"source": "main", "text": "done"},
        )
        yield ChatEvent(
            type="run.finished",
            conversation_id=conversation.id,
            run_id="internal",
            data={"checkpoint_id": "ckpt-1", "tokens": {"input": 30, "output": 12, "total": 42}},
        )

    async def latest_checkpoint_id(self, thread_id: str) -> str | None:
        return "ckpt-1"


async def _chat_service(
    db: SQLiteDatabase,
    tmp_path: Any,
    runtime: SkillRunRuntime,
) -> ChatService:
    settings = Settings.model_validate(
        {
            "chat": {"default_model": "test/model"},
            "memory": {"db_path": str(tmp_path / "attribution.db")},
        }
    )
    return ChatService(
        settings,
        ChatRepository(db),
        DummyAttachments(),  # type: ignore[arg-type]
        DummyRag(),  # type: ignore[arg-type]
        runtime,  # type: ignore[arg-type]
        ChatRunRegistry(),
        TrajectoryStore(db),
        ReplayFixtureStore(settings, db),
        None,  # skill_learning
        _attributor(db),  # skill_execution
    )


async def test_a_completed_chat_run_leaves_a_metrics_row(tmp_path: Any) -> None:
    db = await _db(tmp_path)

    try:
        await _register(db, "demo-skill", "1.2.0")
        service = await _chat_service(db, tmp_path, SkillRunRuntime())
        conversation = await service.create_conversation(ConversationCreate(model="test/model"))
        turn = await service.prepare_message(
            conversation.id, SendMessageRequest(content="use the skill")
        )

        async for _event in service.stream_prepared(turn):
            pass

        rows = await _rows(db)
        assert len(rows) == 1
        assert rows[0]["skill_name"] == "demo-skill"
        assert rows[0]["skill_version"] == "1.2.0"
        assert rows[0]["success"] == 1
        assert rows[0]["tokens_used"] == 42
    finally:
        await db.close()


async def test_a_chat_run_that_never_reads_a_skill_records_nothing(tmp_path: Any) -> None:
    db = await _db(tmp_path)

    try:
        service = await _chat_service(db, tmp_path, SkillRunRuntime(skill_name=None))
        conversation = await service.create_conversation(ConversationCreate(model="test/model"))
        turn = await service.prepare_message(
            conversation.id, SendMessageRequest(content="no skill needed")
        )

        async for _event in service.stream_prepared(turn):
            pass

        assert await _rows(db) == []
    finally:
        await db.close()
