"""Canonical representation for Trajecta verified skills."""

from __future__ import annotations

import hashlib
import json
import re

from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


_SKILL_NAME_RE = re.compile(
    r"^[a-z0-9]+(?:-[a-z0-9]+)*$"
)


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


class SkillStatus(StrEnum):
    CANDIDATE = "candidate"
    EVALUATING = "evaluating"
    VERIFIED = "verified"
    EXPERIMENTING = "experimenting"
    PROMOTED = "promoted"
    ACTIVE = "active"
    REJECTED = "rejected"
    ARCHIVED = "archived"


class SkillRisk(StrEnum):
    READ_ONLY = "read_only"
    SANDBOXED = "sandboxed"
    SENSITIVE = "sensitive"
    EXTERNAL_SIDE_EFFECT = "external_side_effect"


class EvaluationMode(StrEnum):
    SANDBOX = "sandbox"
    OFFLINE = "offline"
    MANUAL = "manual"


class WorkflowStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instruction: str = Field(min_length=1)
    tool_names: list[str] = Field(default_factory=list)
    success_signal: str | None = None
    optional: bool = False


class SkillWorkflow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trigger: str = Field(min_length=1)
    preconditions: list[str] = Field(default_factory=list)
    steps: list[WorkflowStep] = Field(default_factory=list)
    success_criteria: list[str] = Field(default_factory=list)
    failure_recovery: list[str] = Field(default_factory=list)

    @property
    def tool_names(self) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for step in self.steps:
            for name in step.tool_names:
                if name and name not in seen:
                    seen.add(name)
                    result.append(name)
        return result


class OutcomeAssertionType(StrEnum):
    FILE_EXISTS = "file_exists"
    FILE_NOT_EXISTS = "file_not_exists"
    FILE_SHA256 = "file_sha256"
    FILE_CHANGED_FROM_FIXTURE = "file_changed_from_fixture"
    FILE_TEXT_SIMILARITY = "file_text_similarity"
    FILE_JSON_EQUALS = "file_json_equals"
    SQLITE_QUERY_EQUALS = "sqlite_query_equals"
    SQLITE_ROW_COUNT = "sqlite_row_count"
    PDF_SEMANTIC_EQUALS = "pdf_semantic_equals"
    DOCX_SEMANTIC_EQUALS = "docx_semantic_equals"
    XLSX_SEMANTIC_EQUALS = "xlsx_semantic_equals"
    CSV_SEMANTIC_EQUALS = "csv_semantic_equals"
    ZIP_SEMANTIC_EQUALS = "zip_semantic_equals"
    IMAGE_SEMANTIC_EQUALS = "image_semantic_equals"


class ToolAssertionType(StrEnum):
    TOOL_SUCCEEDED = "tool_succeeded"
    TOOL_NOT_USED = "tool_not_used"
    TOOL_CALLS_AT_LEAST = "tool_calls_at_least"
    TOOL_CALLS_AT_MOST = "tool_calls_at_most"


class ToolAssertion(BaseModel):
    """Assertion over observable tool-execution events.

    Important: tool success is evidence that Trajecta invoked a tool and the
    tool reported success. It is NOT independent proof that an external
    service changed. External effects still require read-back/manual
    verification.
    """

    model_config = ConfigDict(extra="forbid")

    type: ToolAssertionType
    tool_name: str = Field(min_length=1)
    required: bool = True
    weight: float = Field(default=1.0, ge=0.0)
    count: int | None = Field(default=None, ge=0)
    result_contains: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class OutcomeAssertion(BaseModel):
    """One deterministic claim about the expected resulting state.

    These assertions describe observable state only.

    They never inspect model reasoning.
    """

    model_config = ConfigDict(extra="forbid")

    type: OutcomeAssertionType
    path: str | None = None
    required: bool = True
    weight: float = Field(default=1.0, ge=0.0)

    # File verification
    expected_sha256: str | None = None
    expected_object_sha256: str | None = None
    similarity_threshold: float = Field(default=0.90, ge=0.0, le=1.0)

    # SQLite verification
    query: str | None = None
    expected_rows: list[list[Any]] | None = None
    expected_row_count: int | None = Field(default=None, ge=0)

    metadata: dict[str, Any] = Field(default_factory=dict)


class SkillEvalCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    task: str = Field(min_length=1)
    rubric: list[str] = Field(default_factory=list)
    expected_contains: list[str] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)
    outcome_assertions: list[OutcomeAssertion] = Field(default_factory=list)
    tool_assertions: list[ToolAssertion] = Field(default_factory=list)
    mode: EvaluationMode = EvaluationMode.SANDBOX
    source_trajectory_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SkillEvalConfig(BaseModel):
    """Promotion thresholds.

    These are initial engineering defaults. Tune them after we have real
    Trajecta evaluation data.
    """

    model_config = ConfigDict(extra="forbid")

    minimum_graded_cases: int = Field(default=2, ge=1)
    minimum_candidate_success_rate: float = Field(default=0.80, ge=0.0, le=1.0)
    maximum_success_rate_regression: float = Field(default=0.0, ge=0.0, le=1.0)
    minimum_success_rate_gain: float = Field(default=0.05, ge=0.0, le=1.0)
    minimum_score_gain: float = Field(default=0.05, ge=0.0, le=1.0)
    minimum_efficiency_gain: float = Field(default=0.10, ge=0.0, le=1.0)
    maximum_token_regression: float = Field(default=0.15, ge=0.0)
    maximum_duration_regression: float = Field(default=0.25, ge=0.0)
    maximum_tool_call_regression: float = Field(default=0.25, ge=0.0)

    repetitions_per_case: int = Field(default=3, ge=1, le=10)
    maximum_score_regression: float = Field(default=0.0, ge=0.0, le=1.0)
    token_efficiency_weight: float = Field(default=0.35, ge=0.0)
    tool_efficiency_weight: float = Field(default=0.30, ge=0.0)
    duration_efficiency_weight: float = Field(default=0.25, ge=0.0)
    retry_efficiency_weight: float = Field(default=0.10, ge=0.0)


class SkillMetadata(BaseModel):
    model_config = ConfigDict(extra="allow")

    source_trajectory_ids: list[str] = Field(default_factory=list)
    held_out_trajectory_ids: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now_iso)
    created_by: str = "trajecta"
    risk: SkillRisk = SkillRisk.READ_ONLY
    mining_fingerprint: str | None = None
    model_name: str | None = None
    notes: dict[str, Any] = Field(default_factory=dict)


class Skill(BaseModel):
    """Canonical Trajecta skill.

    SKILL.md is what Deep Agents reads.
    workflow.yaml, eval.yaml and metadata.json are Trajecta's
    verification/versioning layer.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str = Field(min_length=1)
    instructions: str = Field(min_length=1)
    version: str = "0.1.0"
    status: SkillStatus = SkillStatus.CANDIDATE
    workflow: SkillWorkflow
    eval_config: SkillEvalConfig = Field(default_factory=SkillEvalConfig)
    eval_cases: list[SkillEvalCase] = Field(default_factory=list)
    metadata: SkillMetadata = Field(default_factory=SkillMetadata)
    resources: dict[str, str] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("skill name cannot be empty")
        if len(value) > 64:
            raise ValueError("skill name must be <= 64 characters")
        if not _SKILL_NAME_RE.fullmatch(value):
            raise ValueError(
                "skill name must contain lowercase letters, numbers and single hyphens only"
            )
        return value

    @field_validator("version")
    @classmethod
    def validate_version(cls, value: str) -> str:
        pieces = value.split(".")
        if len(pieces) != 3 or any(not piece.isdigit() for piece in pieces):
            raise ValueError("version must use MAJOR.MINOR.PATCH integers")
        return value

    @field_validator("resources")
    @classmethod
    def validate_resources(cls, value: dict[str, str]) -> dict[str, str]:
        for raw_path in value:
            path = raw_path.replace("\\", "/").strip("/")
            if not path or ".." in Path(path).parts:
                raise ValueError(f"invalid resource path: {raw_path!r}")
            if path == "SKILL.md":
                raise ValueError("resources cannot replace SKILL.md")
        return value

    @model_validator(mode="after")
    def ensure_unique_eval_ids(self) -> Skill:
        ids = [case.id for case in self.eval_cases]
        if len(ids) != len(set(ids)):
            raise ValueError("evaluation case ids must be unique")
        return self

    def to_skill_md(self) -> str:
        """Deep Agents-compatible SKILL.md."""
        frontmatter = yaml.safe_dump(
            {"name": self.name, "description": self.description},
            sort_keys=False,
            allow_unicode=True,
        ).strip()
        return f"---\n{frontmatter}\n---\n\n{self.instructions.strip()}\n"

    def bundle_files(self) -> dict[str, str]:
        """Full Trajecta skill bundle."""
        metadata = {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "status": self.status.value,
            **self.metadata.model_dump(mode="json"),
        }

        eval_payload = {
            "config": self.eval_config.model_dump(mode="json"),
            "cases": [case.model_dump(mode="json") for case in self.eval_cases],
        }

        files = {
            "SKILL.md": self.to_skill_md(),
            "workflow.yaml": yaml.safe_dump(
                self.workflow.model_dump(mode="json"),
                sort_keys=False,
                allow_unicode=True,
            ),
            "metadata.json": json.dumps(metadata, indent=2, ensure_ascii=False),
            "eval.yaml": yaml.safe_dump(
                eval_payload, sort_keys=False, allow_unicode=True
            ),
        }

        for path, content in self.resources.items():
            normalized = path.replace("\\", "/").strip("/")
            files[normalized] = content

        return files

    def content_hash(self) -> str:
        """Stable hash used to detect duplicate candidates."""
        digest = hashlib.sha256()
        for path, content in sorted(self.bundle_files().items()):
            digest.update(path.encode("utf-8"))
            digest.update(b"\0")
            digest.update(content.encode("utf-8"))
            digest.update(b"\0")
        return digest.hexdigest()

    def save(self, root: str | Path) -> Path:
        root_path = Path(root)
        skill_dir = root_path / self.name
        skill_dir.mkdir(parents=True, exist_ok=True)

        for relative, content in self.bundle_files().items():
            target = skill_dir / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

        return skill_dir

    def to_bundle_json(self) -> str:
        return self.model_dump_json()

    @classmethod
    def from_bundle_json(cls, value: str | dict[str, Any]) -> Skill:
        if isinstance(value, str):
            value = json.loads(value)
        return cls.model_validate(value)

    @classmethod
    def load(cls, skill_dir: str | Path) -> Skill:
        path = Path(skill_dir)

        skill_md = path / "SKILL.md"
        workflow_file = path / "workflow.yaml"
        metadata_file = path / "metadata.json"
        eval_file = path / "eval.yaml"

        required = (skill_md, workflow_file, metadata_file, eval_file)
        missing = [item.name for item in required if not item.is_file()]
        if missing:
            raise ValueError(f"invalid skill bundle; missing: {', '.join(missing)}")

        name, description, instructions = cls._parse_skill_md(
            skill_md.read_text(encoding="utf-8")
        )

        workflow_raw = yaml.safe_load(workflow_file.read_text(encoding="utf-8")) or {}
        metadata_raw = json.loads(metadata_file.read_text(encoding="utf-8"))
        eval_raw = yaml.safe_load(eval_file.read_text(encoding="utf-8")) or {}

        reserved = {"name", "description", "version", "status"}
        metadata_payload = {
            key: value for key, value in metadata_raw.items() if key not in reserved
        }

        resources: dict[str, str] = {}
        core = {"SKILL.md", "workflow.yaml", "metadata.json", "eval.yaml"}

        for child in path.rglob("*"):
            if not child.is_file():
                continue
            relative = child.relative_to(path).as_posix()
            if relative in core or relative.startswith("versions/"):
                continue
            resources[relative] = child.read_text(encoding="utf-8")

        return cls(
            name=name,
            description=description,
            instructions=instructions,
            version=str(metadata_raw.get("version", "0.1.0")),
            status=SkillStatus(metadata_raw.get("status", SkillStatus.CANDIDATE.value)),
            workflow=SkillWorkflow.model_validate(workflow_raw),
            eval_config=SkillEvalConfig.model_validate(eval_raw.get("config") or {}),
            eval_cases=[SkillEvalCase.model_validate(item) for item in (eval_raw.get("cases") or [])],
            metadata=SkillMetadata.model_validate(metadata_payload),
            resources=resources,
        )

    @staticmethod
    def _parse_skill_md(content: str) -> tuple[str, str, str]:
        if not content.startswith("---\n"):
            raise ValueError("SKILL.md must start with YAML frontmatter")

        end = content.find("\n---\n", 4)
        if end == -1:
            raise ValueError("SKILL.md frontmatter is not closed")

        frontmatter = yaml.safe_load(content[4:end]) or {}
        name = str(frontmatter.get("name") or "").strip()
        description = str(frontmatter.get("description") or "").strip()
        instructions = content[end + 5 :].strip()

        if not name or not description:
            raise ValueError("SKILL.md frontmatter requires name and description")
        if not instructions:
            raise ValueError("SKILL.md instructions cannot be empty")

        return name, description, instructions
