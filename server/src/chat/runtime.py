from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from deepagents import FilesystemPermission, create_deep_agent
from langchain_core.messages import AIMessageChunk, ToolMessage

from server.src.chat.mcp import MCPToolProvider
from server.src.chat.model import BifrostModelFactory
from server.src.chat.models import Attachment, ChatEvent, Conversation
from server.src.chat.rag import AttachmentRAGIndex
from server.src.config import Settings
from server.src.memory.provider import MemoryProvider

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
- For large indexed documents, use search_attachments to retrieve relevant passages
  before reading entire extracted files. Read the original file when visual/layout
  details matter, especially for images and PDFs.
- For DOCX, prefer the extracted text companion for textual analysis.
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


@dataclass(slots=True)
class PreparedAgentRun:
    agent: Any
    config: dict[str, Any]
    model_name: str
    mcp_tool_count: int
    thread_id: str


class InvalidCheckpoint(RuntimeError):
    pass


class DeepAgentRuntime:
    def __init__(
        self,
        settings: Settings,
        memory: MemoryProvider,
        mcp: MCPToolProvider,
        rag: AttachmentRAGIndex,
    ) -> None:
        self._settings = settings
        self._memory = memory
        self._mcp = mcp
        self._rag = rag
        self._models = BifrostModelFactory(settings)

    async def prepare(
        self,
        *,
        conversation: Conversation,
        thread_id: str,
        model_name: str | None,
        base_checkpoint_id: str | None,
    ) -> PreparedAgentRun:
        """Do all validation that can fail before SSE headers are returned."""
        chosen_model = model_name or conversation.model
        model = self._models.create(chosen_model)
        tools = await self._mcp.get_tools()
        if self._rag.enabled:
            tools = [*tools, self._rag.as_tool(conversation_id=conversation.id)]

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

        agent = create_deep_agent(
            model=model,
            tools=tools,
            system_prompt=SYSTEM_PROMPT,
            permissions=[
                FilesystemPermission(
                    operations=["write"],
                    paths=["/uploads/**"],
                    mode="deny",
                ),
                FilesystemPermission(
                    operations=["write"],
                    paths=["/skills/**"],
                    mode="deny",
                ),
            ],
            **self._memory.agent_kwargs(),
        )
        return PreparedAgentRun(
            agent=agent,
            config=config,
            model_name=chosen_model,
            mcp_tool_count=len(tools) - (1 if self._rag.enabled else 0),
            thread_id=thread_id,
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
        run_id = uuid.uuid4().hex
        content = self._build_input(user_content, attachments)

        yield ChatEvent(
            type="run.started",
            conversation_id=conversation.id,
            run_id=run_id,
            data={
                "model": prepared.model_name,
                "mcp_tool_count": prepared.mcp_tool_count,
                "base_checkpoint_id": prepared.config["configurable"].get("checkpoint_id"),
            },
        )

        try:
            stream = prepared.agent.astream(
                {"messages": [{"role": "user", "content": content}]},
                config=prepared.config,
                stream_mode=["messages", "updates"],
                subgraphs=True,
                version="v2",
                durability="sync",
            )

            async for chunk in stream:
                if cancel_event.is_set():
                    yield ChatEvent(
                        type="run.cancelled",
                        conversation_id=conversation.id,
                        run_id=run_id,
                    )
                    return

                event_type = chunk.get("type")
                namespace = tuple(chunk.get("ns") or ())
                source = _source(namespace)

                if event_type == "messages":
                    token, _metadata = chunk["data"]
                    if isinstance(token, AIMessageChunk):
                        if token.tool_call_chunks:
                            for tool_call in token.tool_call_chunks:
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
                            if text:
                                yield ChatEvent(
                                    type=(
                                        "message.delta"
                                        if source == "main"
                                        else "subagent.message.delta"
                                    ),
                                    conversation_id=conversation.id,
                                    run_id=run_id,
                                    data={"source": source, "text": text},
                                )
                    elif isinstance(token, ToolMessage):
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
                data={"checkpoint_id": checkpoint_id},
            )

        except asyncio.CancelledError:
            raise
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
