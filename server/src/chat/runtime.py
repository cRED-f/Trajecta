from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from deepagents import create_deep_agent
from langchain.agents.middleware import TodoListMiddleware
from langchain_core.messages import AIMessageChunk, ToolMessage
from langgraph.types import Command

from server.src.chat.mcp import MCPToolProvider
from server.src.chat.model import BifrostModelFactory
from server.src.chat.models import Attachment, ChatEvent, Conversation
from server.src.chat.rag import AttachmentRAGIndex
from server.src.config import Settings
from server.src.guardrails.content import (
    ContentGuardrailService,
    GuardrailBlocked,
)
from server.src.guardrails.middleware import (
    GuardrailsModelMiddleware,
)
from server.src.guardrails.policy import PermissionPolicyStore
from server.src.memory.provider import MemoryProvider
from server.src.tools.personal import PersonalToolProvider
from server.src.tools.verification import ConnectorVerificationService

if TYPE_CHECKING:
    from server.src.skills.experiments.service import (
        ExperimentAssignment,
        SkillExperimentService,
    )

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """
You are Trajecta, a local-first autonomous desktop agent.

You are not a simple chatbot. Complete the user's request using reasoning,
memory, verified skills, uploaded files, tools, MCP integrations, and subagents
when they genuinely help.

Behavior:
- Answer simple requests directly; do not create unnecessary plans/subagents.
- Never claim you inspected a file unless you actually used a file or retrieval tool.
- Uploaded files are available under /uploads/ and are immutable.
- User-approved host files live under /workspace/. Prefer Deep Agents built-in file tools there.
- Use persistent memory for stable facts/preferences and session_search for past conversations.
- Use schedule_create for future/recurring work instead of claiming you will remember manually.
- Browser tools automate websites; computer tools (when enabled) control non-browser desktop apps.
- Prefer sandbox execute for code/commands; host process tools are for approved long-running processes only.
- For textual questions about uploaded documents, use one preferred source:
  1) if rag_indexed=true, use search_attachments first;
  2) otherwise, if an extracted text companion exists, read that companion;
  3) use document_read on the original only when extracted text is unavailable,
     incomplete, or you explicitly need to cross-check extraction.
- Do not read both an extracted companion and the original document by default.
- document_read extracts document text/tables; it is not visual PDF inspection.
  For images or genuinely visual/layout-dependent questions, inspect the original
  with a multimodal-capable file tool/model when available.
- Use tools instead of pretending an action was completed.
- If a tool fails, recover or explain the failure rather than fabricating a result.
- Verified skills under /skills/ may be used when relevant.
- Persistent memory under /memories/ may contain useful prior information.
- Do not modify /skills/; Trajecta promotes skills through its verified pipeline.
"""


def _text_from_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    result: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") in {"text", "output_text"}:
            value = block.get("text")
            if isinstance(value, str):
                result.append(value)
    return "".join(result)


def _source(namespace: tuple[str, ...] | list[str]) -> str:
    for segment in namespace:
        if segment.startswith("tools:"):
            return segment
    return "main"


# A provider that stops sending chunks (hung upstream, dropped socket with
# no EOF) fails the run instead of leaving the consumer waiting forever.
MODEL_STREAM_IDLE_TIMEOUT_SECONDS = 180.0


class RunCancelled(Exception):
    """The user pressed Stop while the agent stream was waiting on the model."""


async def _next_or_cancel(
    iterator: AsyncIterator,
    cancel_event: asyncio.Event,
    *,
    idle_timeout_seconds: float = MODEL_STREAM_IDLE_TIMEOUT_SECONDS,
):
    """Return the next stream chunk, or notice Stop / a stalled provider.

    The previous loop only polled ``cancel_event`` when a chunk arrived,
    so Stop did nothing while ``astream()`` was blocked on the model and a
    hung provider stalled the run indefinitely. This races the pending
    ``__anext__`` against the cancel event instead.
    """
    next_chunk = asyncio.create_task(anext(iterator))
    cancelled = asyncio.create_task(cancel_event.wait())

    try:
        done, _pending = await asyncio.wait(
            {next_chunk, cancelled},
            timeout=idle_timeout_seconds,
            return_when=asyncio.FIRST_COMPLETED,
        )
    except asyncio.CancelledError:
        next_chunk.cancel()
        cancelled.cancel()
        await asyncio.gather(next_chunk, cancelled, return_exceptions=True)
        raise

    if not done:
        next_chunk.cancel()
        cancelled.cancel()
        await asyncio.gather(next_chunk, cancelled, return_exceptions=True)
        raise TimeoutError(
            f"No output from the model for {idle_timeout_seconds:g}s"
        )

    if cancelled in done:
        # Stop wins, even if a chunk landed in the same tick.
        next_chunk.cancel()
        await asyncio.gather(next_chunk, return_exceptions=True)
        raise RunCancelled()

    cancelled.cancel()
    await asyncio.gather(cancelled, return_exceptions=True)
    return next_chunk.result()


@dataclass(slots=True)
class PreparedAgentRun:
    agent: Any
    config: dict[str, Any]
    model_name: str
    mcp_tool_count: int
    thread_id: str

    # Which experiment arms this run was assigned to, so chat can attach
    # them to the trajectory before the first metrics row is written.
    skill_assignments: list[ExperimentAssignment] = field(default_factory=list)


class InvalidCheckpoint(RuntimeError):
    pass


class DeepAgentRuntime:
    def __init__(
        self,
        settings: Settings,
        memory: MemoryProvider,
        mcp: MCPToolProvider,
        rag: AttachmentRAGIndex,
        personal_tools: PersonalToolProvider,
        verification: ConnectorVerificationService | None = None,
        permission_policy: PermissionPolicyStore | None = None,
        content_guardrails: ContentGuardrailService | None = None,
        skill_experiments: "SkillExperimentService | None" = None,
    ) -> None:
        self._settings = settings
        self._memory = memory
        self._mcp = mcp
        self._rag = rag
        self._personal_tools = personal_tools
        self._verification = verification
        self._permission_policy = permission_policy
        self._content_guardrails = content_guardrails
        self._skill_experiments = skill_experiments
        self._models = BifrostModelFactory(settings)

    async def prepare(
        self,
        *,
        conversation: Conversation,
        thread_id: str,
        model_name: str | None,
        base_checkpoint_id: str | None,
        task_text: str = "",
    ) -> PreparedAgentRun:
        """Do all validation that can fail before SSE headers are returned."""
        chosen_model = await self._models.resolve_or_default(
            model_name or conversation.model
        )
        model = self._models.create(chosen_model)
        mcp_tools = await self._mcp.get_tools()
        if self._permission_policy is None:
            raise RuntimeError("No permission policy store wired into the runtime")
        policy = await self._permission_policy.snapshot()

        native_tools = self._personal_tools.filter_tools(
            self._personal_tools.get_tools(),
            policy,
        )

        automatic_memory = await self._permission_policy.get_setting(
            "automatic_memory", True
        )
        if not automatic_memory:
            native_tools = [
                tool
                for tool in native_tools
                if tool.name != "memory_save"
            ]

        tools = [*native_tools, *mcp_tools]
        if self._rag.enabled:
            tools.append(self._rag.as_tool(conversation_id=conversation.id))

        config: dict[str, Any] = {
            "configurable": {"thread_id": thread_id}
        }
        if base_checkpoint_id:
            config["configurable"]["checkpoint_id"] = base_checkpoint_id
            checkpointer = self._memory.checkpointer
            if checkpointer is None:
                raise RuntimeError("MemoryProvider checkpointer is not open")
            checkpoint = await checkpointer.aget_tuple(
                {
                    "configurable": {
                        "thread_id": thread_id,
                        "checkpoint_ns": "",
                        "checkpoint_id": base_checkpoint_id,
                    }
                }
            )
            if checkpoint is None:
                raise InvalidCheckpoint(
                    f"Checkpoint {base_checkpoint_id!r} does not exist for this conversation"
                )

        interrupt_policy = self._personal_tools.interrupt_on(policy)

        if self._verification is not None:
            interrupt_policy = interrupt_policy or {}
            interrupt_policy.update(self._verification.interrupt_policy())

        skill_assignments: list[ExperimentAssignment] = []

        if self._skill_experiments is not None:
            skill_assignments = await self._skill_experiments.prepare_task(
                task_text=task_text,
                # Conversation / branch thread gives sticky assignment.
                unit_id=thread_id,
            )

        experiment_prompt = "".join(
            assignment.prompt_override or "" for assignment in skill_assignments
        )

        if self._content_guardrails is None:
            raise RuntimeError(
                "No content guardrail service wired into the runtime"
            )

        system_prompt = SYSTEM_PROMPT + experiment_prompt

        guardrail_middleware = GuardrailsModelMiddleware(
            self._content_guardrails,
            model_name=chosen_model,
        )

        agent = create_deep_agent(
            model=model,
            tools=tools,
            system_prompt=system_prompt,
            middleware=[
                TodoListMiddleware(),
                guardrail_middleware,
            ],
            # IMPORTANT:
            # This remains the tool-authority layer.
            permissions=self._personal_tools.permissions(policy),
            interrupt_on=interrupt_policy or None,
            **self._memory.agent_kwargs(
                allow_execute=policy.mode("terminal") != "deny",
            ),
        )
        return PreparedAgentRun(
            agent=agent,
            config=config,
            model_name=chosen_model,
            mcp_tool_count=len(mcp_tools),
            thread_id=thread_id,
            skill_assignments=skill_assignments,
        )

    async def stream_prepared(
        self,
        *,
        prepared: PreparedAgentRun,
        conversation: Conversation,
        user_content: str,
        attachments: list[Attachment],
        cancel_event: asyncio.Event,
    ) -> AsyncIterator[ChatEvent]:
        content = self._build_input(user_content, attachments)
        async for event in self._stream_input(
            prepared=prepared,
            conversation=conversation,
            agent_input={"messages": [{"role": "user", "content": content}]},
            cancel_event=cancel_event,
            resumed=False,
        ):
            yield event

    async def stream_resume(
        self,
        *,
        prepared: PreparedAgentRun,
        conversation: Conversation,
        decisions: list[dict[str, Any]],
        cancel_event: asyncio.Event,
    ) -> AsyncIterator[ChatEvent]:
        """Resume a Deep Agents HITL interrupt using its persisted checkpoint."""
        async for event in self._stream_input(
            prepared=prepared,
            conversation=conversation,
            agent_input=Command(resume={"decisions": decisions}),
            cancel_event=cancel_event,
            resumed=True,
        ):
            yield event

    @staticmethod
    def _interrupt_value(raw: Any) -> dict[str, Any]:
        items = raw if isinstance(raw, (list, tuple)) else [raw]
        values: list[Any] = []
        for item in items:
            value = getattr(item, "value", item)
            if isinstance(value, dict):
                values.append(value)
            else:
                values.append({"value": str(value)})
        if len(values) == 1 and isinstance(values[0], dict):
            return values[0]
        return {"interrupts": values}

    async def _stream_input(
        self,
        *,
        prepared: PreparedAgentRun,
        conversation: Conversation,
        agent_input: Any,
        cancel_event: asyncio.Event,
        resumed: bool,
    ) -> AsyncIterator[ChatEvent]:
        run_id = uuid.uuid4().hex
        yield ChatEvent(
            type="run.started",
            conversation_id=conversation.id,
            run_id=run_id,
            data={
                "model": prepared.model_name,
                "mcp_tool_count": prepared.mcp_tool_count,
                "base_checkpoint_id": prepared.config["configurable"].get("checkpoint_id"),
                "resumed": resumed,
            },
        )

        # Usage arrives on AIMessageChunk.usage_metadata (same shape the
        # replay evaluator already reads). Summed per chunk, mirroring
        # skills/evaluation/replay.py, so live metrics stay comparable to
        # the evaluator's token counts.
        input_tokens = 0
        output_tokens = 0
        # Runtime statistics reported on run.finished, for experiment arms
        # and the regression monitor.
        loop = asyncio.get_running_loop()
        started_at = loop.time()
        tool_calls = 0
        tool_errors = 0

        try:
            stream = prepared.agent.astream(
                agent_input,
                config=prepared.config,
                stream_mode=["messages", "updates"],
                subgraphs=True,
                version="v2",
                durability="sync",
            )

            iterator = stream.__aiter__()
            while True:
                try:
                    chunk = await _next_or_cancel(iterator, cancel_event)
                except StopAsyncIteration:
                    break
                except RunCancelled:
                    yield ChatEvent(
                        type="run.cancelled",
                        conversation_id=conversation.id,
                        run_id=run_id,
                    )
                    return
                except TimeoutError as exc:
                    logger.warning("Model stream stalled: %s", exc)
                    yield ChatEvent(
                        type="run.error",
                        conversation_id=conversation.id,
                        run_id=run_id,
                        data={
                            "error": str(exc),
                            "code": "model_stream_timeout",
                        },
                    )
                    return

                event_type = chunk.get("type")
                namespace = tuple(chunk.get("ns") or ())
                source = _source(namespace)

                if event_type == "messages":
                    token, _metadata = chunk["data"]
                    if isinstance(token, AIMessageChunk):
                        usage = getattr(token, "usage_metadata", None)

                        if isinstance(usage, Mapping):
                            input_tokens += int(usage.get("input_tokens") or 0)
                            output_tokens += int(usage.get("output_tokens") or 0)

                        if token.tool_call_chunks:
                            for tool_call in token.tool_call_chunks:
                                # Only the first chunk carrying the name is
                                # a new call; later fragments are continuations.
                                if tool_call.get("name"):
                                    tool_calls += 1
                                yield ChatEvent(
                                    type="tool.call.delta",
                                    conversation_id=conversation.id,
                                    run_id=run_id,
                                    data={
                                        "source": source,
                                        "name": tool_call.get("name"),
                                        "id": tool_call.get("id"),
                                        "args": tool_call.get("args"),
                                    },
                                )
                        else:
                            text = _text_from_content(token.content)

                            if text and source == "main":
                                yield ChatEvent(
                                    type="message.delta",
                                    conversation_id=conversation.id,
                                    run_id=run_id,
                                    data={
                                        "source": "main",
                                        "text": text,
                                    },
                                )

                            # Subagent model text does not belong in the
                            # main assistant response.
                    elif isinstance(token, ToolMessage):
                        if getattr(token, "status", None) == "error":
                            tool_errors += 1
                        yield ChatEvent(
                            type="tool.result",
                            conversation_id=conversation.id,
                            run_id=run_id,
                            data={
                                "source": source,
                                "name": token.name,
                                "tool_call_id": token.tool_call_id,
                                "content": str(token.content)[:10_000],
                                "status": getattr(token, "status", None),
                            },
                        )

                elif event_type == "updates":
                    data = chunk.get("data")
                    if isinstance(data, dict):
                        if "__interrupt__" in data:
                            checkpoint_id = await self.latest_checkpoint_id(prepared.thread_id)
                            yield ChatEvent(
                                type="run.interrupted",
                                conversation_id=conversation.id,
                                run_id=run_id,
                                data={
                                    "checkpoint_id": checkpoint_id,
                                    "interrupt": self._interrupt_value(data["__interrupt__"]),
                                },
                            )
                            return
                        for node_name in data:
                            yield ChatEvent(
                                type="agent.step",
                                conversation_id=conversation.id,
                                run_id=run_id,
                                data={
                                    "source": source,
                                    "node": node_name,
                                    "is_subagent": source != "main",
                                },
                            )

            checkpoint_id = await self.latest_checkpoint_id(prepared.thread_id)
            yield ChatEvent(
                type="run.finished",
                conversation_id=conversation.id,
                run_id=run_id,
                data={
                    "checkpoint_id": checkpoint_id,
                    "tokens": {
                        "input": input_tokens,
                        "output": output_tokens,
                        "total": input_tokens + output_tokens,
                    },
                    # Flat keys for experiment arms and the regression
                    # monitor; `tokens` above stays for the dashboard.
                    "duration_seconds": loop.time() - started_at,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "total_tokens": input_tokens + output_tokens,
                    "tool_calls": tool_calls,
                    "tool_errors": tool_errors,
                },
            )

        except asyncio.CancelledError:
            raise
        except GuardrailBlocked as exc:
            # Never log the detected secret/PII itself.
            logger.warning(
                "Deep Agent run blocked by guardrail: "
                "code=%s validator=%s boundary=%s",
                exc.code,
                exc.finding.validator,
                exc.finding.boundary,
            )

            yield ChatEvent(
                type="run.error",
                conversation_id=conversation.id,
                run_id=run_id,
                data={
                    "error": str(exc),
                    "code": exc.code,
                    "guardrail": exc.finding.validator,
                    "boundary": exc.finding.boundary,
                },
            )
        except Exception as exc:
            logger.exception("Deep Agent chat run failed")
            yield ChatEvent(
                type="run.error",
                conversation_id=conversation.id,
                run_id=run_id,
                data={"error": str(exc)},
            )

    async def latest_checkpoint_id(self, thread_id: str) -> str | None:
        checkpointer = self._memory.checkpointer
        if checkpointer is None:
            return None
        checkpoint = await checkpointer.aget_tuple(
            {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
        )
        if checkpoint is None:
            return None
        return checkpoint.config.get("configurable", {}).get("checkpoint_id")

    @staticmethod
    def _build_input(user_content: str, attachments: list[Attachment]) -> str:
        if not attachments:
            return user_content
        sections = [
            user_content.strip(),
            "",
            "The user attached the following files.",
            "Inspect relevant attachments before making claims about their contents.",
            "For attachments marked rag_indexed=true, use search_attachments first for textual questions.",
            "",
        ]
        for attachment in attachments:
            sections.extend(
                [
                    f"- {attachment.filename}",
                    f"  id: {attachment.id}",
                    f"  type: {attachment.kind.value}",
                    f"  original: {attachment.virtual_path}",
                    f"  rag_indexed: {bool(attachment.metadata.get('rag_indexed'))}",
                ]
            )
            if attachment.extracted_virtual_path:
                sections.append(f"  extracted text: {attachment.extracted_virtual_path}")
        return "\n".join(sections)
