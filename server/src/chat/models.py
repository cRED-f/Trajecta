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
    workspace_path: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SelectWorkspaceRequest(BaseModel):
    workspace_path: str


class Conversation(BaseModel):
    id: str
    thread_id: str
    title: str | None
    model: str
    active_branch_id: str | None = None
    archived: bool
    created_at: datetime
    updated_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChatBranch(BaseModel):
    id: str
    conversation_id: str
    thread_id: str
    parent_branch_id: str | None = None
    fork_message_id: str | None = None
    fork_checkpoint_id: str | None = None
    head_checkpoint_id: str | None = None
    label: str | None = None
    created_at: datetime


class ChatMessage(BaseModel):
    id: str
    conversation_id: str
    role: MessageRole
    content: str
    status: MessageStatus
    parent_message_id: str | None = None
    revision_of: str | None = None
    base_checkpoint_id: str | None = None
    checkpoint_id: str | None = None
    created_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


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
    metadata: dict[str, Any] = Field(default_factory=dict)


class ConversationDetail(Conversation):
    branch: ChatBranch | None = None
    messages: list[ChatMessage] = Field(default_factory=list)
    attachments: list[Attachment] = Field(default_factory=list)


class SendMessageRequest(BaseModel):
    content: str = ""
    attachment_ids: list[str] = Field(default_factory=list)
    model: str | None = None


class EditMessageRequest(BaseModel):
    content: str
    attachment_ids: list[str] | None = None
    model: str | None = None


class ResendMessageRequest(BaseModel):
    model: str | None = None


class RegenerateMessageRequest(BaseModel):
    model: str | None = None


class SelectModelRequest(BaseModel):
    model: str


class ModelInfo(BaseModel):
    id: str
    provider: str | None = None
    source: str = "configured"
    owned_by: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ModelCatalog(BaseModel):
    default_model: str
    models: list[ModelInfo] = Field(default_factory=list)
    gateway_reachable: bool = False


class ApprovalDecision(BaseModel):
    type: str
    message: str | None = None
    edited_action: dict[str, Any] | None = None


class ApprovalResumeRequest(BaseModel):
    decisions: list[ApprovalDecision]


class PendingApproval(BaseModel):
    id: str
    conversation_id: str
    branch_id: str
    thread_id: str
    checkpoint_id: str
    user_message_id: str
    model_name: str
    interrupt_data: dict[str, Any] = Field(default_factory=dict)
    partial_text: str = ""
    created_at: datetime
    updated_at: datetime


class CancelRunResponse(BaseModel):
    cancelled: bool


class ChatEvent(BaseModel):
    type: str
    conversation_id: str
    run_id: str
    data: dict[str, Any] = Field(default_factory=dict)
