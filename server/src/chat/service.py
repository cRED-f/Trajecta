from __future__ import annotations

import uuid

from collections.abc import (
    AsyncIterator,
)

from fastapi import UploadFile

from server.src.chat.attachments import (
    AttachmentService,
)

from server.src.chat.mcp import (
    MCPToolProvider,
)

from server.src.chat.models import (
    Attachment,
    ChatEvent,
    ChatMessage,
    Conversation,
    ConversationCreate,
    ConversationDetail,
    MessageRole,
    SendMessageRequest,
)

from server.src.chat.repository import (
    ChatRepository,
)

from server.src.chat.runtime import (
    DeepAgentRuntime,
)

from server.src.chat.runs import (
    ChatRunRegistry,
)

from server.src.config import Settings

from server.src.memory.provider import (
    MemoryProvider,
)


class ConversationNotFound(
    RuntimeError
):
    pass


class InvalidAttachment(
    RuntimeError
):
    pass


class ChatService:
    def __init__(
        self,
        settings: Settings,
        repository: ChatRepository,
        attachments: AttachmentService,
        runtime: DeepAgentRuntime,
        runs: ChatRunRegistry,
    ) -> None:
        self._settings = settings
        self._repository = repository
        self._attachments = attachments
        self._runtime = runtime
        self._runs = runs

    # ------------------------------------------------------------
    # Conversations
    # ------------------------------------------------------------

    async def create_conversation(
        self,
        request: ConversationCreate,
    ) -> Conversation:
        return await self._repository.create_conversation(
            title=request.title,
            model=(
                request.model
                or self._settings.chat.default_model
            ),
            metadata=request.metadata,
        )

    async def list_conversations(
        self,
    ) -> list[Conversation]:
        return await self._repository.list_conversations()

    async def get_conversation(
        self,
        conversation_id: str,
    ) -> ConversationDetail:
        conversation = (
            await self._require_conversation(
                conversation_id
            )
        )

        messages = (
            await self._repository.list_messages(
                conversation_id
            )
        )

        attachments = (
            await self._repository.list_attachments(
                conversation_id
            )
        )

        return ConversationDetail(
            **conversation.model_dump(),
            messages=messages,
            attachments=attachments,
        )

    # ------------------------------------------------------------
    # Attachments
    # ------------------------------------------------------------

    async def upload(
        self,
        conversation_id: str,
        files: list[UploadFile],
    ) -> list[Attachment]:
        await self._require_conversation(
            conversation_id
        )

        results: list[Attachment] = []

        for upload in files:
            attachment = await self._attachments.save(
                conversation_id=conversation_id,
                upload=upload,
            )

            results.append(attachment)

        return results

    # ------------------------------------------------------------
    # Chat
    # ------------------------------------------------------------

    async def stream_message(
        self,
        conversation_id: str,
        request: SendMessageRequest,
    ) -> AsyncIterator[ChatEvent]:
        conversation = (
            await self._require_conversation(
                conversation_id
            )
        )

        attachments = (
            await self._validate_attachments(
                conversation_id,
                request.attachment_ids,
            )
        )

        if (
            not request.content.strip()
            and not attachments
        ):
            raise ValueError(
                "Message must contain text "
                "or at least one attachment"
            )

        user_message = (
            await self._repository.add_message(
                conversation_id=conversation_id,
                role=MessageRole.USER,
                content=request.content,
                metadata={
                    "attachment_ids": (
                        request.attachment_ids
                    )
                },
            )
        )

        await self._repository.bind_attachments(
            attachment_ids=request.attachment_ids,
            message_id=user_message.id,
        )

        # Generate title from first meaningful turn.
        if conversation.title is None:
            title = self._make_title(
                request.content,
                attachments,
            )

            await self._repository.update_title(
                conversation_id,
                title,
            )

        # The service owns the public run id.
        public_run_id = uuid.uuid4().hex

        cancel_event = await self._runs.register(
            conversation_id,
            public_run_id,
        )

        assistant_text: list[str] = []

        try:
            yield ChatEvent(
                type="message.accepted",
                conversation_id=conversation_id,
                run_id=public_run_id,
                data={
                    "message": (
                        user_message.model_dump(
                            mode="json"
                        )
                    )
                },
            )

            async for event in self._runtime.stream_turn(
                conversation=conversation,
                user_content=request.content,
                attachments=attachments,
                model_name=request.model,
                cancel_event=cancel_event,
            ):
                # Normalize DeepAgentRuntime's internal
                # run id to the API run id.
                event.run_id = public_run_id

                if event.type == "message.delta":
                    text = event.data.get(
                        "text"
                    )

                    if isinstance(text, str):
                        assistant_text.append(text)

                yield event

                if event.type in {
                    "run.error",
                    "run.cancelled",
                }:
                    return

            final_text = "".join(
                assistant_text
            ).strip()

            assistant_message = (
                await self._repository.add_message(
                    conversation_id=conversation_id,
                    role=MessageRole.ASSISTANT,
                    content=final_text,
                    parent_message_id=user_message.id,
                    metadata={
                        "model": (
                            request.model
                            or conversation.model
                        ),
                        "run_id": public_run_id,
                    },
                )
            )

            yield ChatEvent(
                type="message.completed",
                conversation_id=conversation_id,
                run_id=public_run_id,
                data={
                    "message": (
                        assistant_message.model_dump(
                            mode="json"
                        )
                    )
                },
            )

        finally:
            await self._runs.unregister(
                conversation_id,
                public_run_id,
            )

    async def cancel(
        self,
        conversation_id: str,
    ) -> bool:
        return await self._runs.cancel(
            conversation_id
        )

    # ------------------------------------------------------------

    async def _require_conversation(
        self,
        conversation_id: str,
    ) -> Conversation:
        result = (
            await self._repository.get_conversation(
                conversation_id
            )
        )

        if result is None:
            raise ConversationNotFound(
                conversation_id
            )

        return result

    async def _validate_attachments(
        self,
        conversation_id: str,
        attachment_ids: list[str],
    ) -> list[Attachment]:
        attachments = (
            await self._repository.get_attachments(
                attachment_ids
            )
        )

        if len(attachments) != len(
            attachment_ids
        ):
            raise InvalidAttachment(
                "One or more attachments "
                "do not exist"
            )

        for attachment in attachments:
            if (
                attachment.conversation_id
                != conversation_id
            ):
                raise InvalidAttachment(
                    "Attachment belongs to "
                    "another conversation"
                )

        return attachments

    @staticmethod
    def _make_title(
        content: str,
        attachments: list[Attachment],
    ) -> str:
        cleaned = " ".join(
            content.split()
        )

        if cleaned:
            return cleaned[:80]

        if attachments:
            return attachments[0].filename[:80]

        return "New conversation"


def build_chat_service(
    settings: Settings,
    memory: MemoryProvider,
) -> ChatService:
    if memory.sqlite is None:
        raise RuntimeError(
            "MemoryProvider must be opened "
            "before ChatService"
        )

    repository = ChatRepository(
        memory.sqlite
    )

    attachment_service = AttachmentService(
        repository,
        uploads_root=(
            settings.chat.uploads_path
        ),
        max_upload_mb=(
            settings.chat.max_upload_mb
        ),
        max_extracted_chars=(
            settings.chat.max_extracted_chars
        ),
    )

    mcp = MCPToolProvider(
        settings
    )

    runtime = DeepAgentRuntime(
        settings,
        memory,
        mcp,
    )

    runs = ChatRunRegistry()

    return ChatService(
        settings,
        repository,
        attachment_service,
        runtime,
        runs,
    )
