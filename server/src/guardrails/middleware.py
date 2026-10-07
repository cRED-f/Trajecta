"""LangChain/Deep Agents model-boundary middleware."""

from __future__ import annotations

import logging

from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware

from langchain_core.messages import (
    BaseMessage,
    SystemMessage,
    ToolMessage,
)

from server.src.guardrails.content import (
    ContentGuardrailService,
)


logger = logging.getLogger(__name__)


class GuardrailsModelMiddleware(
    AgentMiddleware
):
    """Protect every Deep Agents model call before it reaches the model.

    Before model:
        ToolMessage/RAG/file/web -> prompt injection guard
        cloud-bound context -> Secrets + PII
    """

    def __init__(
        self,
        guardrails: ContentGuardrailService,
        *,
        model_name: str,
    ) -> None:
        self._guardrails = guardrails
        self._model_name = model_name

    async def awrap_model_call(
        self,
        request: Any,
        handler: Callable[
            [Any],
            Awaitable[Any],
        ],
    ) -> Any:
        messages: list[BaseMessage] = []

        changed = False

        for message in request.messages:
            guarded = await self._guard_message(
                message
            )

            messages.append(guarded)

            changed = (
                changed
                or guarded is not message
            )

        updates: dict[str, Any] = {}

        if changed:
            updates["messages"] = messages

        # LangChain stores the system message separately.
        system_message = getattr(
            request,
            "system_message",
            None,
        )

        if isinstance(
            system_message,
            SystemMessage,
        ):
            guarded_system = await self._guard_message(
                system_message
            )

            if guarded_system is not system_message:
                updates["system_message"] = (
                    guarded_system
                )

        if updates:
            request = request.override(
                **updates
            )

        return await handler(request)

    async def _guard_message(
        self,
        message: BaseMessage,
    ) -> BaseMessage:
        content = message.content

        is_untrusted = isinstance(
            message,
            ToolMessage,
        )

        if isinstance(content, str):
            guarded = await self._guard_text(
                content,
                untrusted=is_untrusted,
            )

            if guarded == content:
                return message

            return message.model_copy(
                update={
                    "content": guarded,
                }
            )

        if not isinstance(
            content,
            list,
        ):
            return message

        changed = False

        blocks: list[Any] = []

        for block in content:
            if (
                not isinstance(block, dict)
                or block.get("type")
                not in {
                    "text",
                    "output_text",
                }
            ):
                blocks.append(block)

                continue

            text = block.get(
                "text"
            )

            if not isinstance(
                text,
                str,
            ):
                blocks.append(block)

                continue

            guarded = await self._guard_text(
                text,
                untrusted=is_untrusted,
            )

            if guarded != text:
                changed = True

                blocks.append(
                    {
                        **block,
                        "text": guarded,
                    }
                )
            else:
                blocks.append(block)

        if not changed:
            return message

        return message.model_copy(
            update={
                "content": blocks,
            }
        )

    async def _guard_text(
        self,
        text: str,
        *,
        untrusted: bool,
    ) -> str:
        current = text

        if untrusted:
            guarded = (
                await self._guardrails.guard_untrusted_text(
                    current
                )
            )

            current = guarded.text

            for finding in guarded.findings:
                # Never put the actual potentially malicious
                # content in logs.
                logger.warning(
                    "Guardrail %s at %s: action=%s",
                    finding.validator,
                    finding.boundary,
                    finding.action,
                )

        guarded = (
            await self._guardrails.protect_model_context(
                current,
                model_name=self._model_name,
            )
        )

        for finding in guarded.findings:
            # Again: log metadata, never secrets/PII.
            logger.info(
                "Guardrail %s at %s: action=%s",
                finding.validator,
                finding.boundary,
                finding.action,
            )

        return guarded.text
