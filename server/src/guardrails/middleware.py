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
from server.src.config import ContentGuardrailsConfig
from server.src.observability.operations import observed


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

    @observed("trajecta.guardrails.model_boundary")
    async def awrap_model_call(
        self,
        request: Any,
        handler: Callable[
            [Any],
            Awaitable[Any],
        ],
    ) -> Any:
        # One durable SQLite read per model call, not per text block.
        cfg = await self._guardrails.get_settings()
        messages: list[BaseMessage] = []

        changed = False

        for message in request.messages:
            guarded = await self._guard_message(
                message, cfg=cfg
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
                system_message, cfg=cfg
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
        *,
        cfg: ContentGuardrailsConfig,
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
                cfg=cfg,
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
                cfg=cfg,
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
        cfg: ContentGuardrailsConfig,
    ) -> str:
        current = text

        if untrusted:
            guarded = (
                await self._guardrails.guard_untrusted_text(
                    current, cfg=cfg
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
                cfg=cfg,
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
