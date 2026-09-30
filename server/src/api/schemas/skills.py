"""Skill lifecycle request schemas: upgrade, rollback and version compare."""

from __future__ import annotations

from typing import Any, Literal

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


class SkillExperimentCreate(BaseModel):
    skill_name: str = Field(min_length=1)
    control_version: str = Field(min_length=1)
    experiment_version: str = Field(min_length=1)
    traffic_percent: int = Field(ge=1, le=100)


class SkillRegressionCheck(BaseModel):
    stable_version: str = Field(min_length=1)
    current_version: str = Field(min_length=1)


class ExperimentStartRequest(BaseModel):
    """All fields optional — omitted values fall back to config defaults."""

    strategy: Literal["ab", "thompson"] | None = None
    traffic_percent: int | None = Field(default=None, ge=1, le=100)
    auto_stop: bool | None = None
    auto_promote: bool | None = None


class ExperimentArmRequest(BaseModel):
    candidate_id: str = Field(min_length=1)


class ExperimentStopRequest(BaseModel):
    reason: str | None = None


class DependencyRequest(BaseModel):
    depends_on_skill: str = Field(min_length=1)
    version_constraint: str = "*"
    required: bool = True
    metadata: dict[str, Any] | None = None
