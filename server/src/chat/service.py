from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

from fastapi import UploadFile

from server.src.chat.attachments import AttachmentService
from server.src.chat.mcp import MCPToolProvider
from server.src.chat.model import BifrostModelFactory
from server.src.chat.models import (
    Attachment,
    ChatBranch,
    ChatEvent,
    ChatMessage,
    Conversation,
    ConversationCreate,
    ConversationDetail,
    EditMessageRequest,
    MessageRole,
    MessageStatus,
    RegenerateMessageRequest,
    ResendMessageRequest,
    SendMessageRequest,
)
from server.src.chat.rag import AttachmentRAGIndex
from server.src.chat.repository import ChatRepository
from server.src.chat.runtime import DeepAgentRuntime, PreparedAgentRun
from server.src.chat.runs import ChatRunRegistry
from server.src.config import Settings
from server.src.memory.provider import MemoryProvider


class ConversationNotFound(RuntimeError):
    pass


class MessageNotFound(RuntimeError):
    pass


class InvalidAttachment(RuntimeError):
    pass


class InvalidMessageOperation(RuntimeError):
    pass


@dataclass(slots=True)
class PreparedTurn:
    run_id: str
    conversation: Conversation
    branch: ChatBranch
    user_message: ChatMessage
    attachments: list[Attachment]
    runtime: PreparedAgentRun
    cancel_event: asyncio.Event


class ChatService:
    def __init__(
        self,
        settings: Settings,
        repository: ChatRepository,
        attachments: AttachmentService,
        rag: AttachmentRAGIndex,
        runtime: DeepAgentRuntime,
        runs: ChatRunRegistry,
    ) -> None:
        self._settings = settings
        self._models = BifrostModelFactory(settings)
        self._repository = repository
        self._attachments = attachments
        self._rag = rag
        self._runtime = runtime
        self._runs = runs

    # ------------------------------------------------------------------
    # Conversations
    # ------------------------------------------------------------------

    async def create_conversation(self, request: ConversationCreate) -> Conversation:
        return await self._repository.create_conversation(
            title=request.title,
            model=self._models.canonical_model_name(request.model),
            metadata=request.metadata,
        )

    async def list_conversations(self) -> list[Conversation]:
        return await self._repository.list_conversations()

    async def get_conversation(self, conversation_id: str) -> ConversationDetail:
        conversation = await self._require_conversation(conversation_id)
        branch = await self._repository.get_active_branch(conversation_id)
        messages = (
            await self._repository.list_branch_messages(branch.id)
            if branch is not None
            else []
        )
        attachments = await self._repository.list_attachments(conversation_id)
        return ConversationDetail(
            **conversation.model_dump(),
            branch=branch,
            messages=messages,
            attachments=attachments,
        )

    async def list_branches(self, conversation_id: str) -> list[ChatBranch]:
        await self._require_conversation(conversation_id)
        return await self._repository.list_branches(conversation_id)

    async def activate_branch(self, conversation_id: str, branch_id: str) -> ConversationDetail:
        await self._require_conversation(conversation_id)
        branch = await self._repository.get_branch(branch_id)
        if branch is None or branch.conversation_id != conversation_id:
            raise InvalidMessageOperation("Branch does not belong to this conversation")
        await self._repository.set_active_branch(conversation_id, branch_id)
        return await self.get_conversation(conversation_id)

    async def select_model(self, conversation_id: str, model: str) -> Conversation:
        canonical = self._models.canonical_model_name(model)

        await self._require_conversation(conversation_id)
        await self._repository.update_model(conversation_id, canonical)
        result = await self._repository.get_conversation(conversation_id)
        assert result is not None
        return result

    async def delete_conversation(self, conversation_id: str) -> None:
        await self._require_conversation(conversation_id)
        await self._repository.delete_conversation(conversation_id)

    # ------------------------------------------------------------------
    # Attachments
    # ------------------------------------------------------------------

    async def upload(
        self,
        conversation_id: str,
        files: list[UploadFile],
    ) -> list[Attachment]:
        await self._require_conversation(conversation_id)
        results: list[Attachment] = []
        for upload in files:
            attachment = await self._attachments.save(
                conversation_id=conversation_id,
                upload=upload,
            )
            attachment = await self._rag.index_attachment(attachment)
            results.append(attachment)
        return results

    async def resolve_attachment_file(
        self,
        conversation_id: str,
        attachment_id: str,
    ) -> tuple[Attachment, Path]:
        await self._require_conversation(
            conversation_id
        )

        attachment = (
            await self._repository
            .get_attachment(
                attachment_id
            )
        )

        if (
            attachment is None
            or attachment.conversation_id
            != conversation_id
        ):
            raise InvalidAttachment(
                "Attachment not found"
            )

        uploads_root = Path(
            self._settings
            .chat
            .uploads_path
        ).resolve()

        prefix = "/uploads/"

        if not attachment.virtual_path.startswith(
            prefix
        ):
            raise InvalidAttachment(
                "Invalid attachment path"
            )

        relative = (
            attachment.virtual_path[
                len(prefix):
            ]
        )

        path = (
            uploads_root
            / relative
        ).resolve()

        try:
            path.relative_to(
                uploads_root
            )
        except ValueError as exc:
            raise InvalidAttachment(
                "Attachment path escaped upload root"
            ) from exc

        if not path.is_file():
            raise InvalidAttachment(
                "Attachment file does not exist"
            )

        return attachment, path

    # ------------------------------------------------------------------
    # Preflight: all errors that can be known before SSE headers
    # ------------------------------------------------------------------

    async def prepare_message(
        self,
        conversation_id: str,
        request: SendMessageRequest,
    ) -> PreparedTurn:
        conversation = await self._require_conversation(conversation_id)
        branch = await self._require_active_branch(conversation_id)
        attachments = await self._validate_attachments(
            conversation_id,
            request.attachment_ids,
        )
        self._validate_message_content(request.content, attachments)
        base_checkpoint_id = await self._resolve_branch_head(conversation, branch)
        runtime = await self._runtime.prepare(
            conversation=conversation,
            thread_id=branch.thread_id,
            model_name=request.model,
            base_checkpoint_id=base_checkpoint_id,
        )
        return await self._persist_prepared_turn(
            conversation=conversation,
            branch=branch,
            content=request.content,
            attachments=attachments,
            runtime=runtime,
            base_checkpoint_id=base_checkpoint_id,
            model_name=request.model,
            revision_of=None,
            operation="send",
        )

    async def prepare_edit(
        self,
        conversation_id: str,
        message_id: str,
        request: EditMessageRequest,
    ) -> PreparedTurn:
        original = await self._require_user_message(conversation_id, message_id)
        attachments = (
            await self._validate_attachments(conversation_id, request.attachment_ids)
            if request.attachment_ids is not None
            else await self._repository.get_message_attachments(original.id)
        )
        self._validate_message_content(request.content, attachments)
        return await self._prepare_fork_from_user(
            conversation_id=conversation_id,
            original=original,
            content=request.content,
            attachments=attachments,
            model_name=request.model,
            operation="edit",
            revision_of=original.id,
        )

    async def prepare_resend(
        self,
        conversation_id: str,
        message_id: str,
        request: ResendMessageRequest,
    ) -> PreparedTurn:
        original = await self._require_user_message(conversation_id, message_id)
        attachments = await self._repository.get_message_attachments(original.id)
        return await self._prepare_fork_from_user(
            conversation_id=conversation_id,
            original=original,
            content=original.content,
            attachments=attachments,
            model_name=request.model,
            operation="resend",
            revision_of=original.id,
        )

    async def prepare_regenerate(
        self,
        conversation_id: str,
        assistant_message_id: str,
        request: RegenerateMessageRequest,
    ) -> PreparedTurn:
        assistant = await self._require_message(conversation_id, assistant_message_id)
        if assistant.role != MessageRole.ASSISTANT or not assistant.parent_message_id:
            raise InvalidMessageOperation(
                "Regenerate requires an assistant message with a parent user message"
            )
        user = await self._require_user_message(
            conversation_id,
            assistant.parent_message_id,
        )
        attachments = await self._repository.get_message_attachments(user.id)
        return await self._prepare_fork_from_user(
            conversation_id=conversation_id,
            original=user,
            content=user.content,
            attachments=attachments,
            model_name=request.model,
            operation="regenerate",
            revision_of=user.id,
            extra_metadata={"regenerate_of": assistant.id},
        )

    async def _prepare_fork_from_user(
        self,
        *,
        conversation_id: str,
        original: ChatMessage,
        content: str,
        attachments: list[Attachment],
        model_name: str | None,
        operation: str,
        revision_of: str | None,
        extra_metadata: dict[str, str] | None = None,
    ) -> PreparedTurn:
        conversation = await self._require_conversation(conversation_id)
        parent_branch = await self._require_active_branch(conversation_id)
        if not await self._repository.branch_contains_message(parent_branch.id, original.id):
            raise InvalidMessageOperation(
                "The selected message is not part of the active conversation branch"
            )

        base_checkpoint_id = original.base_checkpoint_id

        # A fork at the very first user turn has no checkpoint to rewind to.
        # Reusing the existing thread without checkpoint_id would resume its
        # latest state, so that special case receives a fresh LangGraph thread.
        fork_thread_id = (
            parent_branch.thread_id
            if base_checkpoint_id is not None
            else uuid.uuid4().hex
        )

        runtime = await self._runtime.prepare(
            conversation=conversation,
            thread_id=fork_thread_id,
            model_name=model_name,
            base_checkpoint_id=base_checkpoint_id,
        )
        run_id = uuid.uuid4().hex
        cancel_event = await self._runs.register(conversation_id, run_id)
        try:
            branch = await self._repository.create_branch_before_message(
                conversation_id=conversation_id,
                parent_branch_id=parent_branch.id,
                fork_message_id=original.id,
                fork_checkpoint_id=base_checkpoint_id,
                thread_id=fork_thread_id,
                label=f"{operation}:{original.id[:8]}",
            )
            metadata = {
                "operation": operation,
                "attachment_ids": [item.id for item in attachments],
            }
            if extra_metadata:
                metadata.update(extra_metadata)
            user_message = await self._repository.add_message(
                conversation_id=conversation_id,
                role=MessageRole.USER,
                content=content,
                status=MessageStatus.COMPLETE,
                revision_of=revision_of,
                base_checkpoint_id=base_checkpoint_id,
                metadata=metadata,
                branch_id=branch.id,
            )
            await self._repository.bind_attachments(
                attachment_ids=[item.id for item in attachments],
                message_id=user_message.id,
            )
            return PreparedTurn(
                run_id=run_id,
                conversation=conversation,
                branch=branch,
                user_message=user_message,
                attachments=attachments,
                runtime=runtime,
                cancel_event=cancel_event,
            )
        except Exception:
            await self._runs.unregister(conversation_id, run_id)
            raise

    async def _persist_prepared_turn(
        self,
        *,
        conversation: Conversation,
        branch: ChatBranch,
        content: str,
        attachments: list[Attachment],
        runtime: PreparedAgentRun,
        base_checkpoint_id: str | None,
        model_name: str | None,
        revision_of: str | None,
        operation: str,
    ) -> PreparedTurn:
        run_id = uuid.uuid4().hex
        cancel_event = await self._runs.register(conversation.id, run_id)
        resolved_model = runtime.model_name
        try:
            if conversation.model != resolved_model:
                await self._repository.update_model(
                    conversation.id,
                    resolved_model,
                )
            user_message = await self._repository.add_message(
                conversation_id=conversation.id,
                role=MessageRole.USER,
                content=content,
                status=MessageStatus.COMPLETE,
                revision_of=revision_of,
                base_checkpoint_id=base_checkpoint_id,
                metadata={
                    "attachment_ids": [item.id for item in attachments],
                    "operation": operation,
                    "model": resolved_model,
                },
                branch_id=branch.id,
            )
            await self._repository.bind_attachments(
                attachment_ids=[item.id for item in attachments],
                message_id=user_message.id,
            )
            if conversation.title is None:
                await self._repository.update_title(
                    conversation.id,
                    self._make_title(content, attachments),
                )
            return PreparedTurn(
                run_id=run_id,
                conversation=conversation,
                branch=branch,
                user_message=user_message,
                attachments=attachments,
                runtime=runtime,
                cancel_event=cancel_event,
            )
        except Exception:
            await self._runs.unregister(conversation.id, run_id)
            raise

    # ------------------------------------------------------------------
    # Streaming a prepared turn
    # ------------------------------------------------------------------

    async def stream_prepared(self, turn: PreparedTurn) -> AsyncIterator[ChatEvent]:
        assistant_text: list[str] = []
        final_checkpoint_id: str | None = None
        try:
            yield ChatEvent(
                type="message.accepted",
                conversation_id=turn.conversation.id,
                run_id=turn.run_id,
                data={
                    "message": turn.user_message.model_dump(mode="json"),
                    "branch": turn.branch.model_dump(mode="json"),
                },
            )

            async for event in self._runtime.stream_prepared(
                prepared=turn.runtime,
                conversation=turn.conversation,
                user_content=turn.user_message.content,
                attachments=turn.attachments,
                cancel_event=turn.cancel_event,
            ):
                event.run_id = turn.run_id
                if event.type == "message.delta":
                    text = event.data.get("text")
                    if isinstance(text, str):
                        assistant_text.append(text)
                elif event.type == "run.finished":
                    value = event.data.get("checkpoint_id")
                    if isinstance(value, str):
                        final_checkpoint_id = value
                elif event.type == "run.cancelled":
                    yield event
                    return
                elif event.type == "run.error":
                    yield event
                    return
                yield event

            if final_checkpoint_id is None:
                final_checkpoint_id = await self._runtime.latest_checkpoint_id(
                    turn.branch.thread_id
                )

            final_text = "".join(assistant_text).strip()
            assistant_message = await self._repository.add_message(
                conversation_id=turn.conversation.id,
                role=MessageRole.ASSISTANT,
                content=final_text,
                status=MessageStatus.COMPLETE,
                parent_message_id=turn.user_message.id,
                checkpoint_id=final_checkpoint_id,
                metadata={
                    "model": turn.runtime.model_name,
                    "run_id": turn.run_id,
                },
                branch_id=turn.branch.id,
            )
            await self._repository.update_branch_head(
                turn.branch.id,
                final_checkpoint_id,
            )
            yield ChatEvent(
                type="message.completed",
                conversation_id=turn.conversation.id,
                run_id=turn.run_id,
                data={
                    "message": assistant_message.model_dump(mode="json"),
                    "branch_id": turn.branch.id,
                    "checkpoint_id": final_checkpoint_id,
                },
            )
        finally:
            await self._runs.unregister(turn.conversation.id, turn.run_id)

    async def cancel(self, conversation_id: str) -> bool:
        return await self._runs.cancel(conversation_id)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _require_conversation(self, conversation_id: str) -> Conversation:
        result = await self._repository.get_conversation(conversation_id)
        if result is None:
            raise ConversationNotFound(conversation_id)
        return result

    async def _require_active_branch(self, conversation_id: str) -> ChatBranch:
        branch = await self._repository.get_active_branch(conversation_id)
        if branch is None:
            raise RuntimeError("Conversation does not have an active branch")
        return branch

    async def _require_message(self, conversation_id: str, message_id: str) -> ChatMessage:
        message = await self._repository.get_message(message_id)
        if message is None or message.conversation_id != conversation_id:
            raise MessageNotFound(message_id)
        return message

    async def _require_user_message(self, conversation_id: str, message_id: str) -> ChatMessage:
        message = await self._require_message(conversation_id, message_id)
        if message.role != MessageRole.USER:
            raise InvalidMessageOperation("Only user messages can be edited or resent")
        return message

    async def _validate_attachments(
        self,
        conversation_id: str,
        attachment_ids: list[str] | None,
    ) -> list[Attachment]:
        ids = attachment_ids or []
        attachments = await self._repository.get_attachments(ids)
        if len(attachments) != len(ids):
            raise InvalidAttachment("One or more attachments do not exist")
        for attachment in attachments:
            if attachment.conversation_id != conversation_id:
                raise InvalidAttachment("Attachment belongs to another conversation")
        return attachments

    @staticmethod
    def _validate_message_content(content: str, attachments: list[Attachment]) -> None:
        if not content.strip() and not attachments:
            raise ValueError("Message must contain text or at least one attachment")

    async def _resolve_branch_head(
        self,
        conversation: Conversation,
        branch: ChatBranch,
    ) -> str | None:
        if branch.head_checkpoint_id:
            return branch.head_checkpoint_id
        # Migration compatibility: v2 chats may have checkpoints but no branch head.
        messages = await self._repository.list_branch_messages(branch.id)
        if messages:
            checkpoint = await self._runtime.latest_checkpoint_id(branch.thread_id)
            if checkpoint:
                await self._repository.update_branch_head(branch.id, checkpoint)
                return checkpoint
        return None

    @staticmethod
    def _make_title(content: str, attachments: list[Attachment]) -> str:
        cleaned = " ".join(content.split())
        if cleaned:
            return cleaned[:80]
        if attachments:
            return attachments[0].filename[:80]
        return "New conversation"


def build_chat_service(settings: Settings, memory: MemoryProvider) -> ChatService:
    if memory.sqlite is None:
        raise RuntimeError("MemoryProvider must be opened before ChatService")

    repository = ChatRepository(memory.sqlite)
    attachment_service = AttachmentService(
        repository,
        uploads_root=settings.chat.uploads_path,
        max_upload_mb=settings.chat.max_upload_mb,
        max_extracted_chars=settings.chat.max_extracted_chars,
    )
    rag = AttachmentRAGIndex(
        repository,
        memory.sqlite,
        memory.vector,
        uploads_root=settings.chat.uploads_path,
        config=settings.chat.rag,
    )
    mcp = MCPToolProvider(settings)
    runtime = DeepAgentRuntime(settings, memory, mcp, rag)
    runs = ChatRunRegistry()
    return ChatService(
        settings,
        repository,
        attachment_service,
        rag,
        runtime,
        runs,
    )
