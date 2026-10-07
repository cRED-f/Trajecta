from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

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
from server.src.guardrails.content import (
    ContentGuardrailService,
    GuardrailFinding,
)
from server.src.guardrails.policy import PermissionPolicyStore
from server.src.memory.provider import MemoryProvider
from server.src.tools.personal import PersonalToolProvider
from server.src.skills.evaluation.fixtures import ReplayFixtureStore
from server.src.skills.trajectory_store import TrajectoryStore
from server.src.tools.verification import ConnectorVerificationService

if TYPE_CHECKING:
    from server.src.llm_gateway.settings import LLMSettingsStore
    from server.src.skills.analytics import SkillExecutionAttributor
    from server.src.skills.experiments import SkillExperimentService
    from server.src.skills.learning import SkillLearningCoordinator
    from server.src.skills.service import SkillsService

logger = logging.getLogger(__name__)


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

    guardrail_findings: list[GuardrailFinding] = field(
        default_factory=list
    )


@dataclass(slots=True)
class PreparedResume:
    run_id: str
    conversation: Conversation
    branch: ChatBranch
    user_message: ChatMessage
    runtime: PreparedAgentRun
    cancel_event: asyncio.Event
    decisions: list[dict]
    partial_text: str


class ChatService:
    def __init__(
        self,
        settings: Settings,
        repository: ChatRepository,
        attachments: AttachmentService,
        rag: AttachmentRAGIndex,
        runtime: DeepAgentRuntime,
        runs: ChatRunRegistry,
        trajectories: TrajectoryStore,
        replay_fixtures: ReplayFixtureStore,
        content_guardrails: ContentGuardrailService,
        skill_learning: "SkillLearningCoordinator | None" = None,
        skill_execution: "SkillExecutionAttributor | None" = None,
        skills: "SkillsService | None" = None,
        llm_settings: "LLMSettingsStore | None" = None,
    ) -> None:
        self._settings = settings
        self._models = BifrostModelFactory(settings)
        self._repository = repository
        self._attachments = attachments
        self._rag = rag
        self._runtime = runtime
        self._runs = runs
        self._trajectories = trajectories
        self._replay_fixtures = replay_fixtures
        self._content_guardrails = content_guardrails
        self._skills = skills
        self._llm_settings = llm_settings

        # One service covers everything; the individual collaborators stay
        # overridable so callers that only have an attributor still work.
        if skills is not None:
            if skill_learning is None:
                skill_learning = skills.learning
            if skill_execution is None:
                skill_execution = skills.execution

        self._skill_learning = skill_learning
        self._skill_execution = skill_execution

    # ------------------------------------------------------------------
    # Conversations
    # ------------------------------------------------------------------

    async def create_conversation(self, request: ConversationCreate) -> Conversation:
        model = request.model

        # The global default applies to NEW conversations only; existing
        # conversations keep the model they were created with.
        if model is None and self._llm_settings is not None:
            runtime = await self._llm_settings.get()
            model = runtime["default_model"]

        return await self._repository.create_conversation(
            title=request.title,
            model=self._models.canonical_model_name(model),
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
        await self._require_conversation(conversation_id)
        canonical = await self._models.resolve_or_default(model)
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
        await self._ensure_no_pending_approval(conversation_id)
        conversation = await self._require_conversation(conversation_id)
        branch = await self._require_active_branch(conversation_id)
        attachments = await self._validate_attachments(
            conversation_id,
            request.attachment_ids,
        )
        self._validate_message_content(request.content, attachments)

        guardrail_findings = await self._content_guardrails.inspect_user_prompt(
            request.content or ""
        )

        base_checkpoint_id = await self._resolve_branch_head(conversation, branch)
        runtime = await self._runtime.prepare(
            conversation=conversation,
            thread_id=branch.thread_id,
            model_name=request.model,
            base_checkpoint_id=base_checkpoint_id,
            task_text=request.content or "",
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
            guardrail_findings=guardrail_findings,
        )

    async def prepare_edit(
        self,
        conversation_id: str,
        message_id: str,
        request: EditMessageRequest,
    ) -> PreparedTurn:
        await self._ensure_no_pending_approval(conversation_id)
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
        await self._ensure_no_pending_approval(conversation_id)
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
        await self._ensure_no_pending_approval(conversation_id)
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

        guardrail_findings = await self._content_guardrails.inspect_user_prompt(
            content or ""
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
            task_text=content or "",
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
                guardrail_findings=guardrail_findings or [],
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
        guardrail_findings: list[GuardrailFinding] | None = None,
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
                guardrail_findings=guardrail_findings or [],
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
        final_run_metrics: dict[str, Any] = {}
        # Declared before begin() so the finally below can never see an
        # unbound name if the trajectory write itself fails.
        trajectory_id: str | None = None
        _task_id, trajectory_id = await self._trajectories.begin(
            goal=turn.user_message.content or "[attachment-only task]",
            thread_id=turn.branch.thread_id,
            metadata={
                "conversation_id": turn.conversation.id,
                "branch_id": turn.branch.id,
                "user_message_id": turn.user_message.id,
                "model": turn.runtime.model_name,
                "attachments": [item.id for item in turn.attachments],
            },
        )
        await self._bind_skill_assignments(
            trajectory_id, turn.runtime.skill_assignments
        )
        await self._trajectories.append(
            trajectory_id,
            event_type="user.task",
            data={
                "content": turn.user_message.content[:20_000],
                "attachment_ids": [item.id for item in turn.attachments],
            },
            source="user",
        )
        await self._capture_replay_fixture(
            trajectory_id,
            attachment_paths=[
                path
                for attachment in turn.attachments
                for path in (
                    attachment.virtual_path,
                    attachment.extracted_virtual_path,
                )
                if path
            ],
            metadata={
                "conversation_id": turn.conversation.id,
                "branch_id": turn.branch.id,
                "user_message_id": turn.user_message.id,
            },
        )
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

            for finding in turn.guardrail_findings:
                warning = {
                    "validator": finding.validator,
                    "boundary": finding.boundary,
                    "action": finding.action,
                    "message": finding.message,
                }

                await self._trajectories.append(
                    trajectory_id,
                    event_type="guardrail.warning",
                    data=warning,
                    source="guardrails",
                )

                yield ChatEvent(
                    type="guardrail.warning",
                    conversation_id=turn.conversation.id,
                    run_id=turn.run_id,
                    data=warning,
                )

            async for event in self._runtime.stream_prepared(
                prepared=turn.runtime,
                conversation=turn.conversation,
                user_content=turn.user_message.content,
                attachments=turn.attachments,
                cancel_event=turn.cancel_event,
            ):
                event.run_id = turn.run_id
                if event.type in {
                    "run.started", "tool.call.delta", "tool.result", "agent.step",
                    "run.finished", "run.interrupted", "run.cancelled", "run.error",
                }:
                    await self._trajectories.append(
                        trajectory_id,
                        event_type=event.type,
                        data=event.data,
                        source=str(event.data.get("source") or "main"),
                    )
                if event.type == "message.delta":
                    text = event.data.get("text")
                    if isinstance(text, str):
                        assistant_text.append(text)
                elif event.type == "run.finished":
                    value = event.data.get("checkpoint_id")
                    if isinstance(value, str):
                        final_checkpoint_id = value
                    final_run_metrics = self._run_metrics(event.data)
                elif event.type == "run.interrupted":
                    checkpoint = event.data.get("checkpoint_id")
                    if not isinstance(checkpoint, str) or not checkpoint:
                        checkpoint = await self._runtime.latest_checkpoint_id(turn.branch.thread_id)
                    if not checkpoint:
                        raise RuntimeError("HITL interrupt did not persist a checkpoint")
                    await self._repository.update_branch_head(turn.branch.id, checkpoint)
                    await self._repository.save_pending_approval(
                        conversation_id=turn.conversation.id,
                        branch_id=turn.branch.id,
                        thread_id=turn.branch.thread_id,
                        checkpoint_id=checkpoint,
                        user_message_id=turn.user_message.id,
                        model_name=turn.runtime.model_name,
                        interrupt_data=event.data.get("interrupt") or {},
                        partial_text="".join(assistant_text),
                    )
                    await self._trajectories.finish(
                        trajectory_id, outcome="interrupted", result="Waiting for user approval"
                    )
                    yield event
                    return
                elif event.type == "run.cancelled":
                    await self._trajectories.finish(trajectory_id, outcome="cancelled")
                    yield event
                    return
                elif event.type == "run.error":
                    await self._trajectories.finish(
                        trajectory_id, outcome="failure", result=str(event.data.get("error") or event.data)
                    )
                    await self._complete_runtime_trajectory(
                        trajectory_id, success=False, metrics=final_run_metrics
                    )
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
            await self._capture_success_outcome(trajectory_id)
            await self._trajectories.finish(
                trajectory_id,
                outcome="success",
                result=final_text[:100_000],
                metadata={"checkpoint_id": final_checkpoint_id},
            )
            # Do NOT await mining here. Chat completion only wakes the
            # background worker.
            if self._skill_learning is not None:
                self._skill_learning.notify_success(trajectory_id)
            await self._complete_runtime_trajectory(
                trajectory_id, success=True, metrics=final_run_metrics
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
        except Exception as exc:
            try:
                await self._trajectories.finish(
                    trajectory_id, outcome="failure", result=f"{type(exc).__name__}: {exc}"
                )
                await self._complete_runtime_trajectory(
                    trajectory_id, success=False, metrics=final_run_metrics
                )
            except Exception:
                pass
            raise
        finally:
            await self._attribute_skill_execution(trajectory_id)
            await self._runs.unregister(turn.conversation.id, turn.run_id)

    async def _capture_replay_fixture(
        self,
        trajectory_id: str,
        *,
        attachment_paths: list[str],
        metadata: dict,
    ) -> None:
        """Best-effort initial-state snapshot.

        Failure to capture a fixture must NEVER break the user's real task.
        It only makes future automatic replay unavailable.
        """

        try:
            fixture = await self._replay_fixtures.capture_initial_state(
                trajectory_id=trajectory_id,
                attachment_paths=attachment_paths,
                metadata=metadata,
            )

            if fixture is not None:
                await self._trajectories.append(
                    trajectory_id,
                    event_type="replay.fixture.captured",
                    data={
                        "fixture_id": fixture.id,
                        "complete": fixture.complete,
                        "file_count": fixture.file_count,
                        "total_bytes": fixture.total_bytes,
                    },
                    source="trajecta",
                )

        except Exception as exc:
            await self._trajectories.append(
                trajectory_id,
                event_type="replay.fixture.error",
                data={"error": f"{type(exc).__name__}: {exc}"},
                source="trajecta",
            )

    async def _capture_success_outcome(
        self,
        trajectory_id: str,
    ) -> None:
        """Capture observable post-task state.

        Failure to capture verification evidence must never fail the user's
        actual task.
        """

        try:
            assertions = await self._replay_fixtures.capture_outcome(
                trajectory_id
            )

            await self._trajectories.append(
                trajectory_id,
                event_type="replay.outcome.captured",
                data={
                    "assertion_count": len(assertions),
                    "assertions": [
                        {
                            "type": item.type.value,
                            "path": item.path,
                        }
                        for item in assertions[:100]
                    ],
                },
                source="trajecta",
            )

        except Exception as exc:
            await self._trajectories.append(
                trajectory_id,
                event_type="replay.outcome.error",
                data={"error": f"{type(exc).__name__}: {exc}"},
                source="trajecta",
            )

    async def _attribute_skill_execution(self, trajectory_id: str | None) -> None:
        """Turn the finished trajectory into skill execution metrics.

        Runs in the finally block, after ``finish()`` has already persisted
        the outcome, so the attributor only ever sees a scored run. Failure
        to attribute must NEVER break the user's real task — the metrics
        table is analytics, not the conversation.
        """

        if self._skill_execution is None or trajectory_id is None:
            return

        try:
            await self._skill_execution.attribute(trajectory_id)
        except Exception:
            logger.warning("skill execution attribution failed", exc_info=True)

    async def _bind_skill_assignments(
        self, trajectory_id: str | None, assignments: list[Any]
    ) -> None:
        """Record which experiment arms this run was assigned to.

        Done as soon as the trajectory exists so a metrics row written
        later always has an experiment id to go with it.
        """

        if self._skills is None or trajectory_id is None or not assignments:
            return

        try:
            await self._skills.experiments.bind_trajectory(
                trajectory_id, assignments
            )
        except Exception:
            logger.warning("skill assignment binding failed", exc_info=True)

    async def _complete_runtime_trajectory(
        self,
        trajectory_id: str | None,
        *,
        success: bool,
        metrics: dict[str, Any],
    ) -> None:
        """Close out live metrics for a finished run.

        Analytics, experiment auto-stop and regression monitoring all hang
        off this, and none of them may turn a finished user task into a
        failed one — hence the catch and the event trail.
        """

        if self._skills is None or trajectory_id is None:
            return

        try:
            await self._skills.complete_runtime_trajectory(
                trajectory_id, success=success, metrics=metrics
            )
        except Exception as exc:
            logger.warning("skill runtime completion failed", exc_info=True)
            try:
                await self._trajectories.append(
                    trajectory_id,
                    event_type="skill.runtime.error",
                    data={"error": f"{type(exc).__name__}: {exc}"},
                    source="main",
                )
            except Exception:
                pass

    @staticmethod
    def _run_metrics(data: dict[str, Any]) -> dict[str, Any]:
        """Pull the runtime numbers off a ``run.finished`` payload."""

        return {
            "duration_seconds": data.get("duration_seconds", 0.0),
            "input_tokens": data.get("input_tokens", 0),
            "output_tokens": data.get("output_tokens", 0),
            "tool_calls": data.get("tool_calls", 0),
            "tool_errors": data.get("tool_errors", 0),
        }

    async def stream_resume(self, turn: PreparedResume) -> AsyncIterator[ChatEvent]:
        assistant_text: list[str] = [turn.partial_text]
        final_checkpoint_id: str | None = None
        final_run_metrics: dict[str, Any] = {}
        # Declared before begin() so the finally below can never see an
        # unbound name if the trajectory write itself fails.
        trajectory_id: str | None = None
        _task_id, trajectory_id = await self._trajectories.begin(
            goal=f"Resume approved action for: {turn.user_message.content}",
            thread_id=turn.branch.thread_id,
            metadata={
                "conversation_id": turn.conversation.id,
                "branch_id": turn.branch.id,
                "user_message_id": turn.user_message.id,
                "model": turn.runtime.model_name,
                "approval_resume": True,
                # A resume is only part of the original task; never learn it
                # as a standalone procedure.
                "exclude_from_skill_mining": True,
                "decisions": turn.decisions,
            },
        )
        try:
            async for event in self._runtime.stream_resume(
                prepared=turn.runtime,
                conversation=turn.conversation,
                decisions=turn.decisions,
                cancel_event=turn.cancel_event,
            ):
                event.run_id = turn.run_id
                if event.type in {
                    "run.started", "tool.call.delta", "tool.result", "agent.step",
                    "run.finished", "run.interrupted", "run.cancelled", "run.error",
                }:
                    await self._trajectories.append(
                        trajectory_id,
                        event_type=event.type,
                        data=event.data,
                        source=str(event.data.get("source") or "main"),
                    )
                if event.type == "message.delta":
                    text = event.data.get("text")
                    if isinstance(text, str):
                        assistant_text.append(text)
                elif event.type == "run.finished":
                    value = event.data.get("checkpoint_id")
                    if isinstance(value, str):
                        final_checkpoint_id = value
                    final_run_metrics = self._run_metrics(event.data)
                elif event.type == "run.interrupted":
                    checkpoint = event.data.get("checkpoint_id")
                    if not isinstance(checkpoint, str) or not checkpoint:
                        checkpoint = await self._runtime.latest_checkpoint_id(turn.branch.thread_id)
                    if not checkpoint:
                        raise RuntimeError("HITL interrupt did not persist a checkpoint")
                    await self._repository.update_branch_head(turn.branch.id, checkpoint)
                    await self._repository.save_pending_approval(
                        conversation_id=turn.conversation.id,
                        branch_id=turn.branch.id,
                        thread_id=turn.branch.thread_id,
                        checkpoint_id=checkpoint,
                        user_message_id=turn.user_message.id,
                        model_name=turn.runtime.model_name,
                        interrupt_data=event.data.get("interrupt") or {},
                        partial_text="".join(assistant_text),
                    )
                    await self._trajectories.finish(
                        trajectory_id, outcome="interrupted", result="Waiting for another user approval"
                    )
                    yield event
                    return
                elif event.type in {"run.cancelled", "run.error"}:
                    await self._trajectories.finish(
                        trajectory_id,
                        outcome="cancelled" if event.type == "run.cancelled" else "failure",
                        result=str(event.data.get("error") or event.data),
                    )
                    await self._complete_runtime_trajectory(
                        trajectory_id, success=False, metrics=final_run_metrics
                    )
                    yield event
                    return
                yield event

            if final_checkpoint_id is None:
                final_checkpoint_id = await self._runtime.latest_checkpoint_id(turn.branch.thread_id)
            final_text = "".join(assistant_text).strip()
            assistant_message = await self._repository.add_message(
                conversation_id=turn.conversation.id,
                role=MessageRole.ASSISTANT,
                content=final_text,
                status=MessageStatus.COMPLETE,
                parent_message_id=turn.user_message.id,
                checkpoint_id=final_checkpoint_id,
                metadata={"model": turn.runtime.model_name, "run_id": turn.run_id, "resumed": True},
                branch_id=turn.branch.id,
            )
            await self._repository.update_branch_head(turn.branch.id, final_checkpoint_id)
            await self._repository.clear_pending_approval(turn.conversation.id)
            await self._capture_success_outcome(trajectory_id)
            await self._trajectories.finish(
                trajectory_id, outcome="success", result=final_text[:100_000],
                metadata={"checkpoint_id": final_checkpoint_id, "approval_resume": True},
            )
            await self._complete_runtime_trajectory(
                trajectory_id, success=True, metrics=final_run_metrics
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
        except Exception as exc:
            try:
                await self._trajectories.finish(
                    trajectory_id, outcome="failure", result=f"{type(exc).__name__}: {exc}"
                )
                await self._complete_runtime_trajectory(
                    trajectory_id, success=False, metrics=final_run_metrics
                )
            except Exception:
                pass
            raise
        finally:
            await self._attribute_skill_execution(trajectory_id)
            await self._runs.unregister(turn.conversation.id, turn.run_id)

    async def cancel(self, conversation_id: str) -> bool:
        return await self._runs.cancel(conversation_id)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _ensure_no_pending_approval(self, conversation_id: str) -> None:
        pending = await self._repository.get_pending_approval(conversation_id)
        if pending is not None:
            raise InvalidMessageOperation(
                "This conversation is waiting for approval of a sensitive action. "
                "Approve or reject it before sending another message."
            )

    async def get_pending_approval(self, conversation_id: str) -> dict | None:
        await self._require_conversation(conversation_id)
        return await self._repository.get_pending_approval(conversation_id)

    async def prepare_resume(
        self,
        conversation_id: str,
        decisions: list[dict],
    ) -> PreparedResume:
        conversation = await self._require_conversation(conversation_id)
        pending = await self._repository.get_pending_approval(conversation_id)
        if pending is None:
            raise InvalidMessageOperation("No sensitive action is waiting for approval")
        branch = await self._repository.get_branch(str(pending["branch_id"]))
        if branch is None:
            raise InvalidMessageOperation("Approval branch no longer exists")
        user_message = await self._require_user_message(
            conversation_id, str(pending["user_message_id"])
        )
        interrupt = pending.get("interrupt_data") or {}
        actions = interrupt.get("action_requests") or []
        reviews = interrupt.get("review_configs") or []
        expected = len(actions)
        if expected and len(decisions) != expected:
            raise InvalidMessageOperation(
                f"Expected {expected} approval decision(s), received {len(decisions)}"
            )
        allowed_by_action = {
            str(item.get("action_name")): set(item.get("allowed_decisions") or [])
            for item in reviews
            if isinstance(item, dict)
        }
        known_types = {"approve", "edit", "reject", "respond"}
        for index, decision in enumerate(decisions):
            decision_type = str(decision.get("type") or "")
            if decision_type not in known_types:
                raise InvalidMessageOperation(f"Invalid approval decision type: {decision_type}")
            if index < len(actions) and isinstance(actions[index], dict):
                action_name = str(actions[index].get("name") or "")
                allowed = allowed_by_action.get(action_name)
                if allowed and decision_type not in allowed:
                    raise InvalidMessageOperation(
                        f"Decision {decision_type!r} is not allowed for {action_name!r}"
                    )
            if decision_type == "edit" and not isinstance(decision.get("edited_action"), dict):
                raise InvalidMessageOperation("edit decisions require edited_action")
            if decision_type in {"reject", "respond"} and not str(decision.get("message") or "").strip():
                raise InvalidMessageOperation(f"{decision_type} decisions require a message")
        runtime = await self._runtime.prepare(
            conversation=conversation,
            thread_id=str(pending["thread_id"]),
            model_name=str(pending["model_name"]),
            base_checkpoint_id=str(pending["checkpoint_id"]),
            task_text=str(user_message.content or ""),
        )
        run_id = uuid.uuid4().hex
        cancel_event = await self._runs.register(conversation_id, run_id)
        return PreparedResume(
            run_id=run_id,
            conversation=conversation,
            branch=branch,
            user_message=user_message,
            runtime=runtime,
            cancel_event=cancel_event,
            decisions=decisions,
            partial_text=str(pending.get("partial_text") or ""),
        )

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


def build_chat_service(
    settings: Settings,
    memory: MemoryProvider,
    personal_tools: PersonalToolProvider | None = None,
    connector_verification: ConnectorVerificationService | None = None,
    mcp_tools: MCPToolProvider | None = None,
    permission_policy: PermissionPolicyStore | None = None,
    content_guardrails: ContentGuardrailService | None = None,
    trajectories: TrajectoryStore | None = None,
    replay_fixtures: ReplayFixtureStore | None = None,
    skill_learning: "SkillLearningCoordinator | None" = None,
    skill_execution: "SkillExecutionAttributor | None" = None,
    skills: "SkillsService | None" = None,
    skill_experiments: "SkillExperimentService | None" = None,
    llm_settings: "LLMSettingsStore | None" = None,
) -> ChatService:
    if memory.sqlite is None:
        raise RuntimeError("MemoryProvider must be opened before ChatService")

    if llm_settings is None:
        from server.src.llm_gateway.settings import LLMSettingsStore

        llm_settings = LLMSettingsStore(
            memory.sqlite,
            bootstrap_model=settings.chat.default_model,
        )

    permission_policy = permission_policy or PermissionPolicyStore(memory.sqlite)

    content_guardrails = content_guardrails or ContentGuardrailService(
        settings,
        permission_policy,
    )

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
    verification = connector_verification or ConnectorVerificationService(settings, memory.sqlite)
    if mcp_tools is None:
        from server.src.chat.mcp_settings import MCPToolSettingsStore

        mcp_tools = MCPToolProvider(
            settings,
            MCPToolSettingsStore(memory.sqlite),
            verification=verification,
        )
    mcp = mcp_tools
    tools = personal_tools or PersonalToolProvider(settings, memory)
    skill_experiments = skill_experiments or (
        skills.experiments if skills is not None else None
    )
    runtime = DeepAgentRuntime(
        settings,
        memory,
        mcp,
        rag,
        tools,
        verification=verification,
        permission_policy=permission_policy,
        content_guardrails=content_guardrails,
        skill_experiments=skill_experiments,
    )
    runs = ChatRunRegistry()
    trajectories = trajectories or TrajectoryStore(memory.sqlite)
    replay_fixtures = replay_fixtures or ReplayFixtureStore(settings, memory.sqlite)
    return ChatService(
        settings,
        repository,
        attachment_service,
        rag,
        runtime,
        runs,
        trajectories,
        replay_fixtures,
        content_guardrails,
        skill_learning,
        skill_execution,
        skills,
        llm_settings,
    )
