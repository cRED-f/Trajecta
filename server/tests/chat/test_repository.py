from __future__ import annotations

import pytest

from server.src.chat.models import MessageRole
from server.src.chat.repository import ChatRepository
from server.src.memory.storage.sqlite import SQLiteDatabase


@pytest.mark.asyncio
async def test_branching_preserves_original_history(tmp_path) -> None:
    db = SQLiteDatabase(tmp_path / "chat.db")
    await db.open()
    try:
        repo = ChatRepository(db)
        conversation = await repo.create_conversation(
            title=None,
            model="openai/gpt-4o-mini",
        )
        main = await repo.get_active_branch(conversation.id)
        assert main is not None

        first_user = await repo.add_message(
            conversation_id=conversation.id,
            role=MessageRole.USER,
            content="first question",
            base_checkpoint_id=None,
            branch_id=main.id,
        )
        await repo.add_message(
            conversation_id=conversation.id,
            role=MessageRole.ASSISTANT,
            content="first answer",
            parent_message_id=first_user.id,
            checkpoint_id="ckpt-1",
            branch_id=main.id,
        )
        await repo.update_branch_head(main.id, "ckpt-1")

        second_user = await repo.add_message(
            conversation_id=conversation.id,
            role=MessageRole.USER,
            content="second question",
            base_checkpoint_id="ckpt-1",
            branch_id=main.id,
        )
        await repo.add_message(
            conversation_id=conversation.id,
            role=MessageRole.ASSISTANT,
            content="second answer",
            parent_message_id=second_user.id,
            checkpoint_id="ckpt-2",
            branch_id=main.id,
        )

        branch = await repo.create_branch_before_message(
            conversation_id=conversation.id,
            parent_branch_id=main.id,
            fork_message_id=second_user.id,
            fork_checkpoint_id="ckpt-1",
            label="edit",
        )
        edited = await repo.add_message(
            conversation_id=conversation.id,
            role=MessageRole.USER,
            content="edited second question",
            revision_of=second_user.id,
            base_checkpoint_id="ckpt-1",
            branch_id=branch.id,
        )

        original_history = await repo.list_branch_messages(main.id)
        branch_history = await repo.list_branch_messages(branch.id)

        assert [m.content for m in original_history] == [
            "first question",
            "first answer",
            "second question",
            "second answer",
        ]
        assert [m.content for m in branch_history] == [
            "first question",
            "first answer",
            "edited second question",
        ]
        assert branch.head_checkpoint_id == "ckpt-1"
        assert edited.revision_of == second_user.id

        refreshed = await repo.get_conversation(conversation.id)
        assert refreshed is not None
        assert refreshed.active_branch_id == branch.id
    finally:
        await db.close()
