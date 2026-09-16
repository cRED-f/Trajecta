from __future__ import annotations

from typing import Any

import pytest

from server.src.chat.models import (
    ChatEvent,
    ConversationCreate,
    EditMessageRequest,
    RegenerateMessageRequest,
    SendMessageRequest,
)
from server.src.chat.repository import ChatRepository
from server.src.chat.runs import ChatRunRegistry
from server.src.chat.runtime import PreparedAgentRun
from server.src.chat.service import ChatService, InvalidAttachment
from server.src.config import Settings
from server.src.memory.storage.sqlite import SQLiteDatabase


class DummyAttachments:
    async def save(self, **_: Any) -> Any:
        raise AssertionError("not used")


class DummyRag:
    pass


class FakeRuntime:
    def __init__(self) -> None:
        self.counter = 0

    async def prepare(
        self, *, conversation, thread_id, model_name, base_checkpoint_id
    ):  # noqa: ANN001
        return PreparedAgentRun(
            agent=None,
            config={
                "configurable": {
                    "thread_id": thread_id,
                    **({"checkpoint_id": base_checkpoint_id} if base_checkpoint_id else {}),
                }
            },
            model_name=model_name or conversation.model,
            mcp_tool_count=0,
            thread_id=thread_id,
        )

    async def stream_prepared(self, *, prepared, conversation, user_content, attachments, cancel_event):  # noqa: ANN001
        self.counter += 1
        checkpoint = f"ckpt-{self.counter}"
        yield ChatEvent(
            type="message.delta",
            conversation_id=conversation.id,
            run_id="internal",
            data={"text": f"answer:{user_content}"},
        )
        yield ChatEvent(
            type="run.finished",
            conversation_id=conversation.id,
            run_id="internal",
            data={"checkpoint_id": checkpoint},
        )

    async def latest_checkpoint_id(self, thread_id: str) -> str | None:
        return f"ckpt-{self.counter}" if self.counter else None


async def _consume(service: ChatService, prepared) -> list[ChatEvent]:  # noqa: ANN001
    return [event async for event in service.stream_prepared(prepared)]


@pytest.mark.asyncio
async def test_edit_and_regenerate_create_langgraph_style_branches(tmp_path) -> None:
    db = SQLiteDatabase(tmp_path / "chat.db")
    await db.open()
    try:
        repo = ChatRepository(db)
        runtime = FakeRuntime()
        service = ChatService(
            Settings.model_validate(
                {
                    "chat": {"default_model": "test/model"},
                    "memory": {"db_path": str(tmp_path / "chat.db")},
                }
            ),
            repo,
            DummyAttachments(),  # type: ignore[arg-type]
            DummyRag(),  # type: ignore[arg-type]
            runtime,  # type: ignore[arg-type]
            ChatRunRegistry(),
        )
        conversation = await service.create_conversation(
            ConversationCreate(model="test/model")
        )

        first = await service.prepare_message(
            conversation.id,
            SendMessageRequest(content="original"),
        )
        first_user_id = first.user_message.id
        events = await _consume(service, first)
        first_assistant_id = next(
            event.data["message"]["id"]
            for event in events
            if event.type == "message.completed"
        )

        edited = await service.prepare_edit(
            conversation.id,
            first_user_id,
            EditMessageRequest(content="edited"),
        )
        # Editing the first turn must not reuse the old LangGraph thread without
        # a checkpoint, otherwise it would resume the latest state.
        assert edited.branch.thread_id != conversation.thread_id
        await _consume(service, edited)
        detail = await service.get_conversation(conversation.id)
        assert [message.content for message in detail.messages] == [
            "edited",
            "answer:edited",
        ]
        assert detail.branch is not None
        assert detail.branch.parent_branch_id is not None

        # The old assistant belongs to a non-active branch now and cannot be
        # regenerated from the new branch.
        with pytest.raises(Exception):
            await service.prepare_regenerate(
                conversation.id,
                first_assistant_id,
                RegenerateMessageRequest(),
            )

        active_assistant = detail.messages[-1]
        regenerated = await service.prepare_regenerate(
            conversation.id,
            active_assistant.id,
            RegenerateMessageRequest(),
        )
        await _consume(service, regenerated)
        regenerated_detail = await service.get_conversation(conversation.id)
        assert regenerated_detail.messages[0].revision_of == edited.user_message.id
        assert regenerated_detail.messages[-1].content == "answer:edited"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_invalid_attachment_fails_during_preflight(tmp_path) -> None:
    db = SQLiteDatabase(tmp_path / "chat.db")
    await db.open()
    try:
        repo = ChatRepository(db)
        service = ChatService(
            Settings.model_validate({"chat": {"default_model": "test/model"}}),
            repo,
            DummyAttachments(),  # type: ignore[arg-type]
            DummyRag(),  # type: ignore[arg-type]
            FakeRuntime(),  # type: ignore[arg-type]
            ChatRunRegistry(),
        )
        conversation = await service.create_conversation(ConversationCreate(model="test/model"))
        with pytest.raises(InvalidAttachment):
            await service.prepare_message(
                conversation.id,
                SendMessageRequest(content="hello", attachment_ids=["missing"]),
            )
    finally:
        await db.close()
