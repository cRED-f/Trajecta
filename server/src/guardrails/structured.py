"""Strict JSON + Pydantic validation for internal LLM contracts."""

from __future__ import annotations

import asyncio
import json

from typing import TypeVar

from guardrails.validator_base import (
    FailResult,
)

from guardrails_ai.valid_json import (
    ValidJson,
)

from pydantic import (
    BaseModel,
    ValidationError,
)


T = TypeVar(
    "T",
    bound=BaseModel,
)


class StructuredGuardrailError(
    ValueError
):
    """Internal LLM output failed its machine-readable contract."""


class StructuredOutputGuard:
    """Valid JSON + Pydantic.

    This class NEVER makes an LLM request itself.

    Re-asks must be made by the caller through Bifrost so
    Guardrails cannot create an untracked model side-channel.
    """

    def __init__(
        self,
    ) -> None:
        self._valid_json = (
            ValidJson()
        )

    async def validate_pydantic(
        self,
        raw: str,
        schema: type[T],
    ) -> T:
        text = raw.strip()

        if not text:
            raise StructuredGuardrailError(
                "model returned an empty structured response"
            )

        result = await asyncio.to_thread(
            self._valid_json.validate,
            text,
            {},
        )

        if isinstance(
            result,
            FailResult,
        ):
            raise StructuredGuardrailError(
                result.error_message
                or "model response is not valid JSON"
            )

        try:
            payload = json.loads(
                text
            )

        except json.JSONDecodeError as exc:
            raise StructuredGuardrailError(
                "model response is not valid JSON"
            ) from exc

        try:
            return schema.model_validate(
                payload
            )

        except ValidationError as exc:
            raise StructuredGuardrailError(
                "model JSON failed "
                f"{schema.__name__} validation: {exc}"
            ) from exc
