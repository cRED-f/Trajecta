from __future__ import annotations

from fastapi import (
    APIRouter,
    File,
    HTTPException,
    Request,
    UploadFile,
)

from fastapi.responses import (
    StreamingResponse,
)

from server.src.chat.attachments import (
    AttachmentError,
)

from server.src.chat.models import (
    Attachment,
    CancelRunResponse,
    Conversation,
    ConversationCreate,
    ConversationDetail,
    SendMessageRequest,
)

from server.src.chat.runs import (
    RunAlreadyActive,
)

from server.src.chat.service import (
    ChatService,
    ConversationNotFound,
    InvalidAttachment,
)

from server.src.chat.streaming import (
    sse_stream,
)


router = APIRouter(
    prefix="/chat",
    tags=["chat"],
)


def _service(
    request: Request,
) -> ChatService:
    return request.app.state.chat_service


@router.post(
    "/conversations",
    response_model=Conversation,
)
async def create_conversation(
    body: ConversationCreate,
    request: Request,
) -> Conversation:
    return await _service(
        request
    ).create_conversation(body)


@router.get(
    "/conversations",
    response_model=list[Conversation],
)
async def list_conversations(
    request: Request,
) -> list[Conversation]:
    return await _service(
        request
    ).list_conversations()


@router.get(
    "/conversations/{conversation_id}",
    response_model=ConversationDetail,
)
async def get_conversation(
    conversation_id: str,
    request: Request,
) -> ConversationDetail:
    try:
        return await _service(
            request
        ).get_conversation(
            conversation_id
        )

    except ConversationNotFound:
        raise HTTPException(
            status_code=404,
            detail="Conversation not found",
        )


@router.post(
    "/conversations/{conversation_id}/attachments",
    response_model=list[Attachment],
)
async def upload_attachments(
    conversation_id: str,
    request: Request,
    files: list[UploadFile] = File(...),
) -> list[Attachment]:
    try:
        return await _service(
            request
        ).upload(
            conversation_id,
            files,
        )

    except ConversationNotFound:
        raise HTTPException(
            status_code=404,
            detail="Conversation not found",
        )

    except AttachmentError as exc:
        raise HTTPException(
            status_code=413,
            detail=str(exc),
        )


@router.post(
    "/conversations/{conversation_id}/messages/stream",
)
async def stream_message(
    conversation_id: str,
    body: SendMessageRequest,
    request: Request,
) -> StreamingResponse:
    service = _service(request)

    async def source():
        try:
            async for event in service.stream_message(
                conversation_id,
                body,
            ):
                if await request.is_disconnected():
                    await service.cancel(
                        conversation_id
                    )

                    break

                yield event

        except ConversationNotFound:
            raise

    try:
        stream = sse_stream(
            source(),
            heartbeat_seconds=(
                request.app.state.settings
                .chat
                .stream_heartbeat_seconds
            ),
        )

        return StreamingResponse(
            stream,
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    except RunAlreadyActive:
        raise HTTPException(
            status_code=409,
            detail=(
                "Conversation already has "
                "an active run"
            ),
        )

    except InvalidAttachment as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        )

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        )


@router.post(
    "/conversations/{conversation_id}/cancel",
    response_model=CancelRunResponse,
)
async def cancel_run(
    conversation_id: str,
    request: Request,
) -> CancelRunResponse:
    cancelled = await _service(
        request
    ).cancel(
        conversation_id
    )

    return CancelRunResponse(
        cancelled=cancelled
    )
