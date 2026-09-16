from __future__ import annotations

import json
import uuid

from datetime import UTC, datetime
from typing import Any

from server.src.chat.models import (
    Attachment,
    AttachmentKind,
    AttachmentStatus,
    ChatBranch,
    ChatMessage,
    Conversation,
    MessageRole,
    MessageStatus,
)
from server.src.memory.storage.sqlite import SQLiteDatabase


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _dump(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False)


def _load(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


class ChatRepository:
    def __init__(self, db: SQLiteDatabase) -> None:
        self._db = db

    # ------------------------------------------------------------------
    # Conversations / branches
    # ------------------------------------------------------------------

    async def create_conversation(
        self,
        *,
        title: str | None,
        model: str,
        metadata: dict[str, Any] | None = None,
        conversation_id: str | None = None,
        thread_id: str | None = None,
    ) -> Conversation:
        conversation_id = conversation_id or uuid.uuid4().hex
        thread_id = thread_id or conversation_id
        branch_id = f"{conversation_id}:main"
        now = _now()

        await self._db.execute(
            """
            INSERT INTO conversations (
                id, thread_id, title, model, active_branch_id,
                archived, created_at, updated_at, metadata
            ) VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?)
            """,
            (
                conversation_id,
                thread_id,
                title,
                model,
                branch_id,
                now,
                now,
                _dump(metadata or {}),
            ),
        )
        await self._db.execute(
            """
            INSERT INTO chat_branches (
                id, conversation_id, thread_id, parent_branch_id, fork_message_id,
                fork_checkpoint_id, head_checkpoint_id, label, created_at
            ) VALUES (?, ?, ?, NULL, NULL, NULL, NULL, 'main', ?)
            """,
            (branch_id, conversation_id, thread_id, now),
        )
        result = await self.get_conversation(conversation_id)
        assert result is not None
        return result

    async def get_conversation(self, conversation_id: str) -> Conversation | None:
        row = await self._db.fetchone(
            "SELECT * FROM conversations WHERE id = ?",
            (conversation_id,),
        )
        return self._conversation(row) if row else None

    async def list_conversations(self, *, limit: int = 100) -> list[Conversation]:
        rows = await self._db.fetch(
            """
            SELECT * FROM conversations
            WHERE archived = 0
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (max(1, limit),),
        )
        return [self._conversation(row) for row in rows]

    async def touch_conversation(self, conversation_id: str) -> None:
        await self._db.execute(
            "UPDATE conversations SET updated_at = ? WHERE id = ?",
            (_now(), conversation_id),
        )

    async def update_title(self, conversation_id: str, title: str) -> None:
        await self._db.execute(
            "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
            (title, _now(), conversation_id),
        )

    async def update_model(self, conversation_id: str, model: str) -> None:
        await self._db.execute(
            "UPDATE conversations SET model = ?, updated_at = ? WHERE id = ?",
            (model, _now(), conversation_id),
        )

    async def get_branch(self, branch_id: str) -> ChatBranch | None:
        row = await self._db.fetchone(
            "SELECT * FROM chat_branches WHERE id = ?",
            (branch_id,),
        )
        return self._branch(row) if row else None

    async def list_branches(self, conversation_id: str) -> list[ChatBranch]:
        rows = await self._db.fetch(
            """
            SELECT * FROM chat_branches
            WHERE conversation_id = ?
            ORDER BY created_at ASC
            """,
            (conversation_id,),
        )
        return [self._branch(row) for row in rows]

    async def get_active_branch(self, conversation_id: str) -> ChatBranch | None:
        row = await self._db.fetchone(
            """
            SELECT b.*
            FROM chat_branches b
            JOIN conversations c ON c.active_branch_id = b.id
            WHERE c.id = ?
            """,
            (conversation_id,),
        )
        return self._branch(row) if row else None

    async def set_active_branch(self, conversation_id: str, branch_id: str) -> None:
        await self._db.execute(
            """
            UPDATE conversations
            SET active_branch_id = ?, updated_at = ?
            WHERE id = ?
            """,
            (branch_id, _now(), conversation_id),
        )

    async def update_branch_head(self, branch_id: str, checkpoint_id: str | None) -> None:
        await self._db.execute(
            "UPDATE chat_branches SET head_checkpoint_id = ? WHERE id = ?",
            (checkpoint_id, branch_id),
        )

    async def create_branch_before_message(
        self,
        *,
        conversation_id: str,
        parent_branch_id: str,
        fork_message_id: str,
        fork_checkpoint_id: str | None,
        thread_id: str | None = None,
        label: str | None = None,
    ) -> ChatBranch:
        position_row = await self._db.fetchone(
            """
            SELECT position FROM branch_messages
            WHERE branch_id = ? AND message_id = ?
            """,
            (parent_branch_id, fork_message_id),
        )
        if position_row is None:
            raise ValueError("Message is not part of the selected branch")

        parent_branch = await self.get_branch(parent_branch_id)
        if parent_branch is None:
            raise ValueError("Parent branch does not exist")

        branch_id = uuid.uuid4().hex
        branch_thread_id = thread_id or parent_branch.thread_id
        now = _now()
        await self._db.execute(
            """
            INSERT INTO chat_branches (
                id, conversation_id, thread_id, parent_branch_id, fork_message_id,
                fork_checkpoint_id, head_checkpoint_id, label, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                branch_id,
                conversation_id,
                branch_thread_id,
                parent_branch_id,
                fork_message_id,
                fork_checkpoint_id,
                fork_checkpoint_id,
                label,
                now,
            ),
        )

        # Copy the immutable history strictly before the edited/resend message.
        await self._db.execute(
            """
            INSERT INTO branch_messages(branch_id, message_id, position)
            SELECT ?, message_id, position
            FROM branch_messages
            WHERE branch_id = ? AND position < ?
            ORDER BY position ASC
            """,
            (branch_id, parent_branch_id, position_row["position"]),
        )
        await self.set_active_branch(conversation_id, branch_id)
        result = await self.get_branch(branch_id)
        assert result is not None
        return result

    async def branch_contains_message(self, branch_id: str, message_id: str) -> bool:
        row = await self._db.fetchone(
            "SELECT 1 AS ok FROM branch_messages WHERE branch_id = ? AND message_id = ?",
            (branch_id, message_id),
        )
        return row is not None

    async def list_branch_messages(self, branch_id: str) -> list[ChatMessage]:
        rows = await self._db.fetch(
            """
            SELECT m.*
            FROM branch_messages bm
            JOIN chat_messages m ON m.id = bm.message_id
            WHERE bm.branch_id = ?
            ORDER BY bm.position ASC
            """,
            (branch_id,),
        )
        return [self._message(row) for row in rows]

    async def list_messages(self, conversation_id: str) -> list[ChatMessage]:
        branch = await self.get_active_branch(conversation_id)
        if branch is None:
            return []
        return await self.list_branch_messages(branch.id)

    async def append_message_to_branch(self, branch_id: str, message_id: str) -> None:
        row = await self._db.fetchone(
            "SELECT COALESCE(MAX(position), -1) + 1 AS next_pos FROM branch_messages WHERE branch_id = ?",
            (branch_id,),
        )
        position = int(row["next_pos"] if row else 0)
        await self._db.execute(
            """
            INSERT INTO branch_messages(branch_id, message_id, position)
            VALUES (?, ?, ?)
            """,
            (branch_id, message_id, position),
        )

    # ------------------------------------------------------------------
    # Messages
    # ------------------------------------------------------------------

    async def add_message(
        self,
        *,
        conversation_id: str,
        role: MessageRole,
        content: str,
        status: MessageStatus = MessageStatus.COMPLETE,
        parent_message_id: str | None = None,
        revision_of: str | None = None,
        base_checkpoint_id: str | None = None,
        checkpoint_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        message_id: str | None = None,
        created_at: str | None = None,
        branch_id: str | None = None,
    ) -> ChatMessage:
        message_id = message_id or uuid.uuid4().hex
        now = created_at or _now()
        await self._db.execute(
            """
            INSERT INTO chat_messages (
                id, conversation_id, role, content, status,
                parent_message_id, revision_of, base_checkpoint_id,
                checkpoint_id, created_at, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                message_id,
                conversation_id,
                role.value,
                content,
                status.value,
                parent_message_id,
                revision_of,
                base_checkpoint_id,
                checkpoint_id,
                now,
                _dump(metadata or {}),
            ),
        )
        if branch_id is not None:
            await self.append_message_to_branch(branch_id, message_id)
        await self.touch_conversation(conversation_id)
        result = await self.get_message(message_id)
        assert result is not None
        return result

    async def get_message(self, message_id: str) -> ChatMessage | None:
        row = await self._db.fetchone(
            "SELECT * FROM chat_messages WHERE id = ?",
            (message_id,),
        )
        return self._message(row) if row else None

    async def update_message_status(self, message_id: str, status: MessageStatus) -> None:
        await self._db.execute(
            "UPDATE chat_messages SET status = ? WHERE id = ?",
            (status.value, message_id),
        )

    async def update_message_checkpoint(self, message_id: str, checkpoint_id: str | None) -> None:
        await self._db.execute(
            "UPDATE chat_messages SET checkpoint_id = ? WHERE id = ?",
            (checkpoint_id, message_id),
        )

    # ------------------------------------------------------------------
    # Attachments
    # ------------------------------------------------------------------

    async def add_attachment(
        self,
        *,
        conversation_id: str,
        filename: str,
        mime_type: str | None,
        kind: AttachmentKind,
        virtual_path: str,
        extracted_virtual_path: str | None,
        size_bytes: int,
        sha256: str,
        status: AttachmentStatus,
        metadata: dict[str, Any] | None = None,
        attachment_id: str | None = None,
    ) -> Attachment:
        attachment_id = attachment_id or uuid.uuid4().hex
        now = _now()
        await self._db.execute(
            """
            INSERT INTO attachments (
                id, conversation_id, message_id, filename, mime_type, kind,
                virtual_path, extracted_virtual_path, size_bytes, sha256,
                status, created_at, metadata
            ) VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                attachment_id,
                conversation_id,
                filename,
                mime_type,
                kind.value,
                virtual_path,
                extracted_virtual_path,
                size_bytes,
                sha256,
                status.value,
                now,
                _dump(metadata or {}),
            ),
        )
        result = await self.get_attachment(attachment_id)
        assert result is not None
        return result

    async def get_attachment(self, attachment_id: str) -> Attachment | None:
        row = await self._db.fetchone(
            "SELECT * FROM attachments WHERE id = ?",
            (attachment_id,),
        )
        return self._attachment(row) if row else None

    async def get_attachments(self, attachment_ids: list[str]) -> list[Attachment]:
        if not attachment_ids:
            return []
        placeholders = ",".join("?" for _ in attachment_ids)
        rows = await self._db.fetch(
            f"SELECT * FROM attachments WHERE id IN ({placeholders})",
            tuple(attachment_ids),
        )
        mapping = {row["id"]: self._attachment(row) for row in rows}
        return [mapping[item] for item in attachment_ids if item in mapping]

    async def get_message_attachments(self, message_id: str) -> list[Attachment]:
        rows = await self._db.fetch(
            """
            SELECT a.*
            FROM message_attachments ma
            JOIN attachments a ON a.id = ma.attachment_id
            WHERE ma.message_id = ?
            ORDER BY a.created_at ASC
            """,
            (message_id,),
        )
        return [self._attachment(row) for row in rows]

    async def list_attachments(self, conversation_id: str) -> list[Attachment]:
        rows = await self._db.fetch(
            "SELECT * FROM attachments WHERE conversation_id = ? ORDER BY created_at ASC",
            (conversation_id,),
        )
        return [self._attachment(row) for row in rows]

    async def bind_attachments(self, *, attachment_ids: list[str], message_id: str) -> None:
        for attachment_id in attachment_ids:
            await self._db.execute(
                """
                INSERT OR IGNORE INTO message_attachments(message_id, attachment_id)
                VALUES (?, ?)
                """,
                (message_id, attachment_id),
            )
            # Keep the legacy owner column populated for compatibility.
            await self._db.execute(
                """
                UPDATE attachments
                SET message_id = COALESCE(message_id, ?)
                WHERE id = ?
                """,
                (message_id, attachment_id),
            )

    async def update_attachment_metadata(self, attachment_id: str, metadata: dict[str, Any]) -> None:
        await self._db.execute(
            "UPDATE attachments SET metadata = ? WHERE id = ?",
            (_dump(metadata), attachment_id),
        )

    # ------------------------------------------------------------------
    # Mappers
    # ------------------------------------------------------------------

    @staticmethod
    def _conversation(row: dict[str, Any]) -> Conversation:
        return Conversation(
            id=row["id"],
            thread_id=row["thread_id"],
            title=row["title"],
            model=row["model"],
            active_branch_id=row.get("active_branch_id"),
            archived=bool(row["archived"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            metadata=_load(row["metadata"]),
        )

    @staticmethod
    def _branch(row: dict[str, Any]) -> ChatBranch:
        return ChatBranch(
            id=row["id"],
            conversation_id=row["conversation_id"],
            thread_id=row.get("thread_id") or row["conversation_id"],
            parent_branch_id=row["parent_branch_id"],
            fork_message_id=row["fork_message_id"],
            fork_checkpoint_id=row["fork_checkpoint_id"],
            head_checkpoint_id=row["head_checkpoint_id"],
            label=row["label"],
            created_at=row["created_at"],
        )

    @staticmethod
    def _message(row: dict[str, Any]) -> ChatMessage:
        return ChatMessage(
            id=row["id"],
            conversation_id=row["conversation_id"],
            role=MessageRole(row["role"]),
            content=row["content"],
            status=MessageStatus(row["status"]),
            parent_message_id=row["parent_message_id"],
            revision_of=row.get("revision_of"),
            base_checkpoint_id=row.get("base_checkpoint_id"),
            checkpoint_id=row.get("checkpoint_id"),
            created_at=row["created_at"],
            metadata=_load(row["metadata"]),
        )

    @staticmethod
    def _attachment(row: dict[str, Any]) -> Attachment:
        return Attachment(
            id=row["id"],
            conversation_id=row["conversation_id"],
            message_id=row["message_id"],
            filename=row["filename"],
            mime_type=row["mime_type"],
            kind=AttachmentKind(row["kind"]),
            virtual_path=row["virtual_path"],
            extracted_virtual_path=row["extracted_virtual_path"],
            size_bytes=row["size_bytes"],
            sha256=row["sha256"],
            status=AttachmentStatus(row["status"]),
            created_at=row["created_at"],
            metadata=_load(row["metadata"]),
        )
