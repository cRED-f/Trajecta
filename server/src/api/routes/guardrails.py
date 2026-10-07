"""Content/privacy guardrail settings.

These controls never authorize tools.

Tool execution remains controlled by the existing
/permissions ALLOW / ASK / DENY + HITL system.
"""

from __future__ import annotations

from typing import Literal

from fastapi import (
    APIRouter,
    Request,
)

from pydantic import (
    BaseModel,
    Field,
)


router = APIRouter(
    prefix="/guardrails",
    tags=["guardrails"],
)


class ContentGuardrailsPatch(
    BaseModel
):
    enabled: bool | None = None

    prompt_injection_enabled: (
        bool | None
    ) = None

    prompt_injection_heuristic_threshold: (
        float | None
    ) = Field(
        default=None,
        ge=0.0,
        le=1.0,
    )

    user_prompt_action: (
        Literal["warn", "block"]
        | None
    ) = None

    untrusted_content_action: (
        Literal["warn", "block"]
        | None
    ) = None

    jailbreak_enabled: (
        bool | None
    ) = None

    jailbreak_threshold: (
        float | None
    ) = Field(
        default=None,
        ge=0.0,
        le=1.0,
    )

    secrets_enabled: (
        bool | None
    ) = None

    pii_enabled: (
        bool | None
    ) = None

    pii_entities: (
        list[str]
        | None
    ) = None

    local_providers: (
        list[str]
        | None
    ) = None

    cloud_sensitive_action: (
        Literal[
            "allow",
            "redact",
            "block",
        ]
        | None
    ) = None


def _service(
    request: Request,
):
    return (
        request.app.state
        .content_guardrails
    )


@router.get("")
async def get_guardrails(
    request: Request,
) -> dict:
    content = (
        await _service(
            request
        ).get_settings()
    )

    return {
        "content": content.model_dump(
            mode="json"
        )
    }


@router.patch("")
async def update_guardrails(
    body: ContentGuardrailsPatch,
    request: Request,
) -> dict:
    content = (
        await _service(
            request
        ).update_settings(
            body.model_dump(
                exclude_none=True,
                mode="python",
            )
        )
    )

    return {
        "content": content.model_dump(
            mode="json"
        )
    }
