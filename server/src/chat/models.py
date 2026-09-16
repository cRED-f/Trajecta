from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


class MessageStatus(StrEnum):
    PENDING = "pending"
    STREAMING = "streaming"
    COMPLETE = "complete"
    ERROR = "error"
    CANCELLED = "cancelled"


class AttachmentKind(StrEnum):
    IMAGE = "image"
    PDF = "pdf"
    DOCX = "docx"
    TEXT = "text"
    DOCUMENT = "document"
    OTHER = "other"


class AttachmentStatus(StrEnum):
    PROCESSING = "processing"
    READY = "ready"
    ERROR = "error"


class ConversationCreate(BaseModel):
    title: str | None = None
    model: str | None = None

    metadata: dict[str, Any] = Field(
        default_factory=dict
    )


class Conversation(BaseModel):
    id: str
    thread_id: str

    title: str | None
    model: str

    archived: bool

    created_at: datetime
    updated_at: datetime

    metadata: dict[str, Any] = Field(
        default_factory=dict
    )


class ChatMessage(BaseModel):
    id: str
    conversation_id: str

    role: MessageRole
    content: str

    status: MessageStatus

    parent_message_id: str | None = None

    created_at: datetime

    metadata: dict[str, Any] = Field(
        default_factory=dict
    )


class Attachment(BaseModel):
    id: str
    conversation_id: str

    message_id: str | None = None

    filename: str

    mime_type: str | None = None
    kind: AttachmentKind

    virtual_path: str
    extracted_virtual_path: str | None = None

    size_bytes: int
    sha256: str

    status: AttachmentStatus

    created_at: datetime

    metadata: dict[str, Any] = Field(
        default_factory=dict
    )


class ConversationDetail(Conversation):
    messages: list[ChatMessage] = Field(
        default_factory=list
    )

    attachments: list[Attachment] = Field(
        default_factory=list
    )


class SendMessageRequest(BaseModel):
    content: str = ""

    attachment_ids: list[str] = Field(
        default_factory=list
    )

    # Override the conversation model for this turn.
    model: str | None = None


class CancelRunResponse(BaseModel):
    cancelled: bool


class ChatEvent(BaseModel):
    type: str

    conversation_id: str
    run_id: str

    data: dict[str, Any] = Field(
        default_factory=dict
    )
