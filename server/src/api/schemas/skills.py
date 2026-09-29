"""Skill lifecycle request schemas: upgrade, rollback and version compare."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SkillUpgradeRequest(BaseModel):
    candidate_id: str = Field(min_length=1)
    reason: str | None = None


class SkillRollbackRequest(BaseModel):
    version: str | None = None
    reason: str | None = None


class SkillVersionCompareRequest(BaseModel):
    from_version: str
    to_version: str
