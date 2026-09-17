from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse

from server.src.chat.attachments import AttachmentError
from server.src.chat.model import BifrostConfigurationError
from server.src.chat.models import (
    ApprovalResumeRequest,
    Attachment,
    CancelRunResponse,
    ChatBranch,
    Conversation,
    ConversationCreate,
    ConversationDetail,
    EditMessageRequest,
    RegenerateMessageRequest,
    ResendMessageRequest,
    SelectModelRequest,
    SendMessageRequest,
)
from server.src.chat.runtime import InvalidCheckpoint
from server.src.chat.runs import RunAlreadyActive
from server.src.chat.service import (
    ChatService,
    ConversationNotFound,
    InvalidAttachment,
    InvalidMessageOperation,
    MessageNotFound,
    PreparedResume,
    PreparedTurn,
)
from server.src.chat.streaming import sse_stream

router = APIRouter(prefix="/chat", tags=["chat"])


def _service(request: Request) -> ChatService:
    return request.app.state.chat_service


def _raise_preflight(exc: Exception) -> None:
    if isinstance(exc, ConversationNotFound):
        raise HTTPException(status_code=404, detail="Conversation not found") from exc
    if isinstance(exc, MessageNotFound):
        raise HTTPException(status_code=404, detail="Message not found") from exc
    if isinstance(exc, RunAlreadyActive):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, (InvalidAttachment, InvalidMessageOperation, InvalidCheckpoint, ValueError)):
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if isinstance(exc, BifrostConfigurationError):
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    raise exc


def _response_for_prepared(
    request: Request,
    service: ChatService,
    prepared: PreparedTurn,
) -> StreamingResponse:
    async def source() -> AsyncIterator:
        async for event in service.stream_prepared(prepared):
            if await request.is_disconnected():
                await service.cancel(prepared.conversation.id)
                break
            yield event

    return StreamingResponse(
        sse_stream(
            source(),
            heartbeat_seconds=(
                request.app.state.settings.chat.stream_heartbeat_seconds
            ),
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _response_for_resume(
    request: Request,
    service: ChatService,
    prepared: PreparedResume,
) -> StreamingResponse:
    async def source() -> AsyncIterator:
        async for event in service.stream_resume(prepared):
            if await request.is_disconnected():
                await service.cancel(prepared.conversation.id)
                break
            yield event

    return StreamingResponse(
        sse_stream(
            source(),
            heartbeat_seconds=request.app.state.settings.chat.stream_heartbeat_seconds,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/conversations", response_model=Conversation)
async def create_conversation(
    body: ConversationCreate,
    request: Request,
) -> Conversation:
    return await _service(request).create_conversation(body)


@router.get("/conversations", response_model=list[Conversation])
async def list_conversations(request: Request) -> list[Conversation]:
    return await _service(request).list_conversations()


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(
    conversation_id: str,
    request: Request,
) -> ConversationDetail:
    try:
        return await _service(request).get_conversation(conversation_id)
    except Exception as exc:
        _raise_preflight(exc)
        raise AssertionError("unreachable")


@router.get(
    "/conversations/{conversation_id}/branches",
    response_model=list[ChatBranch],
)
async def list_conversation_branches(
    conversation_id: str,
    request: Request,
) -> list[ChatBranch]:
    try:
        return await _service(request).list_branches(conversation_id)
    except Exception as exc:
        _raise_preflight(exc)
        raise AssertionError("unreachable")


@router.put(
    "/conversations/{conversation_id}/branches/{branch_id}/activate",
    response_model=ConversationDetail,
)
async def activate_conversation_branch(
    conversation_id: str,
    branch_id: str,
    request: Request,
) -> ConversationDetail:
    try:
        return await _service(request).activate_branch(conversation_id, branch_id)
    except Exception as exc:
        _raise_preflight(exc)
        raise AssertionError("unreachable")


@router.put("/conversations/{conversation_id}/model", response_model=Conversation)
async def select_conversation_model(
    conversation_id: str,
    body: SelectModelRequest,
    request: Request,
) -> Conversation:
    try:
        return await _service(request).select_model(conversation_id, body.model)
    except Exception as exc:
        _raise_preflight(exc)
        raise AssertionError("unreachable")


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
        return await _service(request).upload(conversation_id, files)
    except ConversationNotFound as exc:
        raise HTTPException(status_code=404, detail="Conversation not found") from exc
    except AttachmentError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc


@router.post("/conversations/{conversation_id}/messages/stream")
async def stream_message(
    conversation_id: str,
    body: SendMessageRequest,
    request: Request,
) -> StreamingResponse:
    service = _service(request)
    try:
        prepared = await service.prepare_message(conversation_id, body)
    except Exception as exc:
        _raise_preflight(exc)
        raise AssertionError("unreachable")
    return _response_for_prepared(request, service, prepared)


@router.post("/conversations/{conversation_id}/messages/{message_id}/edit/stream")
async def edit_message(
    conversation_id: str,
    message_id: str,
    body: EditMessageRequest,
    request: Request,
) -> StreamingResponse:
    service = _service(request)
    try:
        prepared = await service.prepare_edit(conversation_id, message_id, body)
    except Exception as exc:
        _raise_preflight(exc)
        raise AssertionError("unreachable")
    return _response_for_prepared(request, service, prepared)


@router.post("/conversations/{conversation_id}/messages/{message_id}/resend/stream")
async def resend_message(
    conversation_id: str,
    message_id: str,
    body: ResendMessageRequest,
    request: Request,
) -> StreamingResponse:
    service = _service(request)
    try:
        prepared = await service.prepare_resend(conversation_id, message_id, body)
    except Exception as exc:
        _raise_preflight(exc)
        raise AssertionError("unreachable")
    return _response_for_prepared(request, service, prepared)


@router.post("/conversations/{conversation_id}/messages/{message_id}/regenerate/stream")
async def regenerate_message(
    conversation_id: str,
    message_id: str,
    body: RegenerateMessageRequest,
    request: Request,
) -> StreamingResponse:
    service = _service(request)
    try:
        prepared = await service.prepare_regenerate(conversation_id, message_id, body)
    except Exception as exc:
        _raise_preflight(exc)
        raise AssertionError("unreachable")
    return _response_for_prepared(request, service, prepared)


@router.get("/conversations/{conversation_id}/approval")
async def get_pending_approval(conversation_id: str, request: Request) -> dict:
    try:
        pending = await _service(request).get_pending_approval(conversation_id)
    except Exception as exc:
        _raise_preflight(exc)
        raise AssertionError("unreachable")
    if pending is None:
        raise HTTPException(status_code=404, detail="No pending approval")
    return pending


@router.post("/conversations/{conversation_id}/approval/stream")
async def resume_pending_approval(
    conversation_id: str,
    body: ApprovalResumeRequest,
    request: Request,
) -> StreamingResponse:
    service = _service(request)
    decisions = [item.model_dump(exclude_none=True) for item in body.decisions]
    try:
        prepared = await service.prepare_resume(conversation_id, decisions)
    except Exception as exc:
        _raise_preflight(exc)
        raise AssertionError("unreachable")
    return _response_for_resume(request, service, prepared)


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(
    conversation_id: str,
    request: Request,
) -> None:
    try:
        await _service(request).delete_conversation(conversation_id)
    except ConversationNotFound as exc:
        raise HTTPException(status_code=404, detail="Conversation not found") from exc


@router.post(
    "/conversations/{conversation_id}/cancel",
    response_model=CancelRunResponse,
)
async def cancel_run(
    conversation_id: str,
    request: Request,
) -> CancelRunResponse:
    cancelled = await _service(request).cancel(conversation_id)
    return CancelRunResponse(cancelled=cancelled)


@router.get(
    "/conversations/"
    "{conversation_id}/"
    "attachments/"
    "{attachment_id}/content"
)
async def attachment_content(
    conversation_id: str,
    attachment_id: str,
    request: Request,
) -> FileResponse:
    try:
        attachment, path = (
            await _service(request)
            .resolve_attachment_file(
                conversation_id,
                attachment_id,
            )
        )

    except Exception as exc:
        _raise_preflight(exc)
        raise AssertionError(
            "unreachable"
        )

    return FileResponse(
        path=path,
        media_type=(
            attachment.mime_type
            or "application/octet-stream"
        ),
        headers={
            "Content-Disposition":
                f'inline; filename="'
                f'{attachment.filename}"'
        },
    )
