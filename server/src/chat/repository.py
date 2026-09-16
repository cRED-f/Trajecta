from __future__ import annotations

import json
import uuid

from datetime import UTC, datetime
from typing import Any

from server.src.chat.models import (
    Attachment,
    AttachmentKind,
    AttachmentStatus,
    ChatMessage,
    Conversation,
    MessageRole,
    MessageStatus,
)

from server.src.memory.storage.sqlite import (
    SQLiteDatabase,
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _dump(value: dict[str, Any]) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
    )


def _load(value: str | None) -> dict[str, Any]:
    if not value:
        return {}

    try:
        parsed = json.loads(value)

        return (
            parsed
            if isinstance(parsed, dict)
            else {}
        )

    except json.JSONDecodeError:
        return {}


class ChatRepository:
    def __init__(
        self,
        db: SQLiteDatabase,
    ) -> None:
        self._db = db

    # ------------------------------------------------------------
    # Conversations
    # ------------------------------------------------------------

    async def create_conversation(
        self,
        *,
        title: str | None,
        model: str,
        metadata: dict[str, Any] | None = None,
        conversation_id: str | None = None,
        thread_id: str | None = None,
    ) -> Conversation:
        conversation_id = (
            conversation_id or uuid.uuid4().hex
        )
        thread_id = (
            thread_id or conversation_id
        )

        now = _now()

        await self._db.execute(
            """
            INSERT INTO conversations (
                id,
                thread_id,
                title,
                model,
                archived,
                created_at,
                updated_at,
                metadata
            )
            VALUES (?, ?, ?, ?, 0, ?, ?, ?)
            """,
            (
                conversation_id,
                thread_id,
                title,
                model,
                now,
                now,
                _dump(metadata or {}),
            ),
        )

        conversation = await self.get_conversation(
            conversation_id
        )

        assert conversation is not None

        return conversation

    async def get_conversation(
        self,
        conversation_id: str,
    ) -> Conversation | None:
        row = await self._db.fetchone(
            """
            SELECT *
            FROM conversations
            WHERE id = ?
            """,
            (conversation_id,),
        )

        if row is None:
            return None

        return self._conversation(row)

    async def list_conversations(
        self,
        *,
        limit: int = 100,
    ) -> list[Conversation]:
        rows = await self._db.fetch(
            """
            SELECT *
            FROM conversations
            WHERE archived = 0
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (max(1, limit),),
        )

        return [
            self._conversation(row)
            for row in rows
        ]

    async def touch_conversation(
        self,
        conversation_id: str,
    ) -> None:
        await self._db.execute(
            """
            UPDATE conversations
            SET updated_at = ?
            WHERE id = ?
            """,
            (
                _now(),
                conversation_id,
            ),
        )

    async def update_title(
        self,
        conversation_id: str,
        title: str,
    ) -> None:
        await self._db.execute(
            """
            UPDATE conversations
            SET title = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                title,
                _now(),
                conversation_id,
            ),
        )

    # ------------------------------------------------------------
    # Messages
    # ------------------------------------------------------------

    async def add_message(
        self,
        *,
        conversation_id: str,
        role: MessageRole,
        content: str,
        status: MessageStatus = MessageStatus.COMPLETE,
        parent_message_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        message_id: str | None = None,
        created_at: str | None = None,
    ) -> ChatMessage:
        message_id = (
            message_id or uuid.uuid4().hex
        )
        now = created_at or _now()

        await self._db.execute(
            """
            INSERT INTO chat_messages (
                id,
                conversation_id,
                role,
                content,
                status,
                parent_message_id,
                created_at,
                metadata
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                message_id,
                conversation_id,
                role.value,
                content,
                status.value,
                parent_message_id,
                now,
                _dump(metadata or {}),
            ),
        )

        await self.touch_conversation(
            conversation_id
        )

        message = await self.get_message(
            message_id
        )

        assert message is not None

        return message

    async def get_message(
        self,
        message_id: str,
    ) -> ChatMessage | None:
        row = await self._db.fetchone(
            """
            SELECT *
            FROM chat_messages
            WHERE id = ?
            """,
            (message_id,),
        )

        if row is None:
            return None

        return self._message(row)

    async def list_messages(
        self,
        conversation_id: str,
    ) -> list[ChatMessage]:
        rows = await self._db.fetch(
            """
            SELECT *
            FROM chat_messages
            WHERE conversation_id = ?
            ORDER BY created_at ASC
            """,
            (conversation_id,),
        )

        return [
            self._message(row)
            for row in rows
        ]

    # ------------------------------------------------------------
    # Attachments
    # ------------------------------------------------------------

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
        attachment_id = (
            attachment_id or uuid.uuid4().hex
        )

        now = _now()

        await self._db.execute(
            """
            INSERT INTO attachments (
                id,
                conversation_id,
                message_id,
                filename,
                mime_type,
                kind,
                virtual_path,
                extracted_virtual_path,
                size_bytes,
                sha256,
                status,
                created_at,
                metadata
            )
            VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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

        attachment = await self.get_attachment(
            attachment_id
        )

        assert attachment is not None

        return attachment

    async def get_attachment(
        self,
        attachment_id: str,
    ) -> Attachment | None:
        row = await self._db.fetchone(
            """
            SELECT *
            FROM attachments
            WHERE id = ?
            """,
            (attachment_id,),
        )

        if row is None:
            return None

        return self._attachment(row)

    async def get_attachments(
        self,
        attachment_ids: list[str],
    ) -> list[Attachment]:
        if not attachment_ids:
            return []

        placeholders = ",".join(
            "?"
            for _ in attachment_ids
        )

        rows = await self._db.fetch(
            f"""
            SELECT *
            FROM attachments
            WHERE id IN ({placeholders})
            """,
            tuple(attachment_ids),
        )

        mapping = {
            row["id"]: self._attachment(row)
            for row in rows
        }

        return [
            mapping[attachment_id]
            for attachment_id in attachment_ids
            if attachment_id in mapping
        ]

    async def list_attachments(
        self,
        conversation_id: str,
    ) -> list[Attachment]:
        rows = await self._db.fetch(
            """
            SELECT *
            FROM attachments
            WHERE conversation_id = ?
            ORDER BY created_at ASC
            """,
            (conversation_id,),
        )

        return [
            self._attachment(row)
            for row in rows
        ]

    async def bind_attachments(
        self,
        *,
        attachment_ids: list[str],
        message_id: str,
    ) -> None:
        if not attachment_ids:
            return

        placeholders = ",".join(
            "?"
            for _ in attachment_ids
        )

        await self._db.execute(
            f"""
            UPDATE attachments
            SET message_id = ?
            WHERE id IN ({placeholders})
            """,
            (
                message_id,
                *attachment_ids,
            ),
        )

    # ------------------------------------------------------------
    # Mapping
    # ------------------------------------------------------------

    @staticmethod
    def _conversation(
        row: dict[str, Any],
    ) -> Conversation:
        return Conversation(
            id=row["id"],
            thread_id=row["thread_id"],
            title=row["title"],
            model=row["model"],
            archived=bool(row["archived"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            metadata=_load(row["metadata"]),
        )

    @staticmethod
    def _message(
        row: dict[str, Any],
    ) -> ChatMessage:
        return ChatMessage(
            id=row["id"],
            conversation_id=row["conversation_id"],
            role=MessageRole(row["role"]),
            content=row["content"],
            status=MessageStatus(row["status"]),
            parent_message_id=row["parent_message_id"],
            created_at=row["created_at"],
            metadata=_load(row["metadata"]),
        )

    @staticmethod
    def _attachment(
        row: dict[str, Any],
    ) -> Attachment:
        return Attachment(
            id=row["id"],
            conversation_id=row["conversation_id"],
            message_id=row["message_id"],
            filename=row["filename"],
            mime_type=row["mime_type"],
            kind=AttachmentKind(row["kind"]),
            virtual_path=row["virtual_path"],
            extracted_virtual_path=row[
                "extracted_virtual_path"
            ],
            size_bytes=row["size_bytes"],
            sha256=row["sha256"],
            status=AttachmentStatus(row["status"]),
            created_at=row["created_at"],
            metadata=_load(row["metadata"]),
        )
