from __future__ import annotations

import asyncio
import logging
import uuid

from collections.abc import AsyncIterator
from typing import Any

from deepagents import (
    FilesystemPermission,
    create_deep_agent,
)

from langchain_core.messages import (
    AIMessageChunk,
    ToolMessage,
)

from server.src.chat.mcp import (
    MCPToolProvider,
)

from server.src.chat.model import (
    BifrostModelFactory,
)

from server.src.chat.models import (
    Attachment,
    ChatEvent,
    Conversation,
)

from server.src.config import Settings

from server.src.memory.provider import (
    MemoryProvider,
)


logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """
You are Trajecta, a local-first autonomous desktop agent.

You are not a simple chatbot. Your job is to complete the user's request
using reasoning, memory, verified skills, files, tools, MCP integrations,
and subagents whenever they are useful.

Behavior:

- Answer simple requests directly.
- Do not create unnecessary plans or subagents.
- For complex work, decompose the task when decomposition genuinely helps.
- Never claim you inspected a file unless you actually used read_file or
  another relevant tool.
- Uploaded files are available under /uploads/.
- Uploaded files are immutable. Never attempt to edit or delete /uploads/.
- When a DOCX has an extracted text companion, prefer the extracted text.
- For PDFs and images, use read_file when visual/document inspection is needed.
- Use tools instead of pretending that an action was completed.
- If a tool fails, explain or recover rather than fabricating a result.
- Verified skills under /skills/ may be used when relevant.
- Persistent memory under /memories/ may contain useful information from
  previous interactions.
- Do not modify /skills/. Trajecta promotes skills through its own verified
  evaluation pipeline.
"""


def _text_from_content(
    content: Any,
) -> str:
    if isinstance(content, str):
        return content

    if not isinstance(content, list):
        return ""

    result: list[str] = []

    for block in content:
        if not isinstance(block, dict):
            continue

        if block.get("type") in {
            "text",
            "output_text",
        }:
            value = block.get("text")

            if isinstance(value, str):
                result.append(value)

    return "".join(result)


def _source(
    namespace: tuple[str, ...] | list[str],
) -> str:
    for segment in namespace:
        if segment.startswith("tools:"):
            return segment

    return "main"


class DeepAgentRuntime:
    def __init__(
        self,
        settings: Settings,
        memory: MemoryProvider,
        mcp: MCPToolProvider,
    ) -> None:
        self._settings = settings
        self._memory = memory
        self._mcp = mcp

        self._models = BifrostModelFactory(
            settings
        )

    async def stream_turn(
        self,
        *,
        conversation: Conversation,
        user_content: str,
        attachments: list[Attachment],
        model_name: str | None,
        cancel_event: asyncio.Event,
    ) -> AsyncIterator[ChatEvent]:
        run_id = uuid.uuid4().hex

        tools = await self._mcp.get_tools()

        model = self._models.create(
            model_name
            or conversation.model
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

        content = self._build_input(
            user_content,
            attachments,
        )

        yield ChatEvent(
            type="run.started",
            conversation_id=conversation.id,
            run_id=run_id,
            data={
                "model": (
                    model_name
                    or conversation.model
                ),
                "mcp_tool_count": len(tools),
            },
        )

        config = {
            "configurable": {
                "thread_id": conversation.thread_id,
            }
        }

        try:
            stream = agent.astream(
                {
                    "messages": [
                        {
                            "role": "user",
                            "content": content,
                        }
                    ]
                },
                config=config,
                stream_mode=[
                    "messages",
                    "updates",
                ],
                subgraphs=True,
                version="v2",
            )

            async for chunk in stream:
                if cancel_event.is_set():
                    yield ChatEvent(
                        type="run.cancelled",
                        conversation_id=conversation.id,
                        run_id=run_id,
                    )

                    return

                event_type = chunk.get(
                    "type"
                )

                namespace = tuple(
                    chunk.get("ns") or ()
                )

                source = _source(namespace)

                # ------------------------------------------------
                # Streaming model/tool messages
                # ------------------------------------------------

                if event_type == "messages":
                    token, metadata = chunk[
                        "data"
                    ]

                    if isinstance(
                        token,
                        AIMessageChunk,
                    ):
                        if token.tool_call_chunks:
                            for tool_call in (
                                token.tool_call_chunks
                            ):
                                yield ChatEvent(
                                    type="tool.call.delta",
                                    conversation_id=conversation.id,
                                    run_id=run_id,
                                    data={
                                        "source": source,
                                        "name": (
                                            tool_call.get(
                                                "name"
                                            )
                                        ),
                                        "id": (
                                            tool_call.get(
                                                "id"
                                            )
                                        ),
                                        "args": (
                                            tool_call.get(
                                                "args"
                                            )
                                        ),
                                    },
                                )

                        else:
                            text = _text_from_content(
                                token.content
                            )

                            if text:
                                event_name = (
                                    "message.delta"
                                    if source == "main"
                                    else
                                    "subagent.message.delta"
                                )

                                yield ChatEvent(
                                    type=event_name,
                                    conversation_id=conversation.id,
                                    run_id=run_id,
                                    data={
                                        "source": source,
                                        "text": text,
                                    },
                                )

                    elif isinstance(
                        token,
                        ToolMessage,
                    ):
                        yield ChatEvent(
                            type="tool.result",
                            conversation_id=conversation.id,
                            run_id=run_id,
                            data={
                                "source": source,
                                "name": token.name,
                                "tool_call_id": (
                                    token.tool_call_id
                                ),
                                "content": str(
                                    token.content
                                )[:10_000],
                                "status": getattr(
                                    token,
                                    "status",
                                    None,
                                ),
                            },
                        )

                # ------------------------------------------------
                # Node/subagent progress
                # ------------------------------------------------

                elif event_type == "updates":
                    data = chunk.get(
                        "data"
                    )

                    if not isinstance(
                        data,
                        dict,
                    ):
                        continue

                    for node_name in data:
                        yield ChatEvent(
                            type="agent.step",
                            conversation_id=conversation.id,
                            run_id=run_id,
                            data={
                                "source": source,
                                "node": node_name,
                                "is_subagent": (
                                    source != "main"
                                ),
                            },
                        )

            yield ChatEvent(
                type="run.finished",
                conversation_id=conversation.id,
                run_id=run_id,
            )

        except asyncio.CancelledError:
            raise

        except Exception as exc:
            logger.exception(
                "Deep Agent chat run failed"
            )

            yield ChatEvent(
                type="run.error",
                conversation_id=conversation.id,
                run_id=run_id,
                data={
                    "error": str(exc),
                },
            )

    @staticmethod
    def _build_input(
        user_content: str,
        attachments: list[Attachment],
    ) -> str:
        if not attachments:
            return user_content

        sections = [
            user_content.strip(),
            "",
            "The user attached the following files.",
            "Inspect relevant attachments with read_file "
            "before making claims about their contents.",
            "",
        ]

        for attachment in attachments:
            sections.append(
                f"- {attachment.filename}"
            )

            sections.append(
                f"  type: {attachment.kind.value}"
            )

            sections.append(
                f"  original: {attachment.virtual_path}"
            )

            if (
                attachment.extracted_virtual_path
                is not None
            ):
                sections.append(
                    "  extracted text: "
                    f"{attachment.extracted_virtual_path}"
                )

        return "\n".join(sections)
