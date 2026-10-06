from __future__ import annotations

import pytest

from pydantic import (
    BaseModel,
    Field,
)

from server.src.guardrails.structured import (
    StructuredGuardrailError,
    StructuredOutputGuard,
)


class Decision(BaseModel):
    success: bool

    score: float = Field(
        ge=0.0,
        le=1.0,
    )


@pytest.mark.asyncio
async def test_valid_json_and_pydantic_pass() -> None:
    guard = StructuredOutputGuard()

    value = await guard.validate_pydantic(
        '{"success": true, "score": 0.8}',
        Decision,
    )

    assert value.success is True
    assert value.score == 0.8


@pytest.mark.asyncio
async def test_markdown_fenced_json_fails_strict_contract() -> None:
    guard = StructuredOutputGuard()

    with pytest.raises(StructuredGuardrailError):
        await guard.validate_pydantic(
            (
                "```json\n"
                '{"success": true, "score": 0.8}'
                "\n```"
            ),
            Decision,
        )


@pytest.mark.asyncio
async def test_schema_failure_is_not_silently_coerced() -> None:
    guard = StructuredOutputGuard()

    with pytest.raises(StructuredGuardrailError):
        await guard.validate_pydantic(
            '{"success": true, "score": 2.0}',
            Decision,
        )
