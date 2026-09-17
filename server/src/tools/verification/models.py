"""Verified connector action models: receipts, comparison results and statuses.

A mutating connector (MCP) tool is wrapped so that after executing the action
Trajecta extracts a resource/receipt ID, persists an ``ActionReceipt``, invokes
an independent read tool and compares the actual state against the requested
state before reporting the action as verified.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


class VerificationStatus(StrEnum):
    ACTION_FAILED = "action_failed"
    UNVERIFIED = "unverified"
    VERIFIED = "verified"
    VERIFICATION_FAILED = "verification_failed"
    NEEDS_REVIEW = "needs_review"


class ComparisonResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actual_path: str
    operator: str
    required: bool
    passed: bool
    expected: Any = None
    actual: Any = None
    reason: str = ""


class ActionReceipt(BaseModel):
    """Persistent record of one verified mutating connector action."""

    model_config = ConfigDict(extra="forbid")

    id: str
    connector: str
    action_tool: str
    status: VerificationStatus

    resource_type: str | None = None
    resource_id: str | None = None

    action_args: dict[str, Any] = Field(default_factory=dict)
    action_result: Any = None

    readback_tool: str | None = None
    readback_args: dict[str, Any] = Field(default_factory=dict)
    readback_result: Any = None

    comparisons: list[ComparisonResult] = Field(default_factory=list)

    created_at: str = Field(default_factory=utc_now_iso)
    verified_at: str | None = None

    metadata: dict[str, Any] = Field(default_factory=dict)


class VerifiedActionResult(BaseModel):
    """What the model sees after a mutating verified connector call."""

    ok: bool
    verification_status: VerificationStatus
    receipt_id: str
    resource_id: str | None = None
    action_result: Any = None
    readback_result: Any = None
    comparisons: list[ComparisonResult] = Field(default_factory=list)
    message: str