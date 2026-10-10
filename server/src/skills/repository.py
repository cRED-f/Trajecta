"""SQLite persistence for Trajecta's verified-skill lifecycle."""

from __future__ import annotations

import json
import uuid

from datetime import UTC, datetime
from typing import Any

from server.src.memory.storage.sqlite import SQLiteDatabase
from server.src.skills.representation.skill import Skill, SkillStatus


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


class SkillRepository:
    def __init__(self, db: SQLiteDatabase) -> None:
        self._db = db

    # -------------------------------------------------
    # Candidates
    # -------------------------------------------------

    async def create_candidate(self, skill: Skill) -> dict[str, Any]:
        candidate_id = uuid.uuid4().hex
        now = _now()

        skill = skill.model_copy(update={"status": SkillStatus.CANDIDATE})

        metadata = {
            "skill_bundle": skill.model_dump(mode="json"),
            "content_hash": skill.content_hash(),
        }

        await self._db.execute(
            """
            INSERT INTO skill_candidates(
                id, name, description, content, status,
                source_trajectory_ids, created_at, updated_at, metadata
            ) VALUES(?, ?, ?, ?, 'candidate', ?, ?, ?, ?)
            """,
            (
                candidate_id,
                skill.name,
                skill.description,
                skill.instructions,
                json.dumps(skill.metadata.source_trajectory_ids, ensure_ascii=False),
                now,
                now,
                json.dumps(metadata, ensure_ascii=False),
            ),
        )

        return await self.get_candidate(candidate_id) or {}

    async def get_candidate(self, candidate_id: str) -> dict[str, Any] | None:
        row = await self._db.fetchone(
            "SELECT * FROM skill_candidates WHERE id = ?", (candidate_id,)
        )
        if row is None:
            return None
        return self._decode_candidate(row)

    async def get_candidate_skill(self, candidate_id: str) -> Skill | None:
        record = await self.get_candidate(candidate_id)
        if record is None:
            return None
        bundle = record["metadata"].get("skill_bundle")
        if not isinstance(bundle, dict):
            return None
        skill = Skill.model_validate(bundle)
        return skill.model_copy(update={"status": SkillStatus(record["status"])})

    async def list_candidates(
        self, *, status: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 500))

        if status:
            rows = await self._db.fetch(
                """
                SELECT * FROM skill_candidates
                WHERE status = ?
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (status, limit),
            )
        else:
            rows = await self._db.fetch(
                """
                SELECT * FROM skill_candidates
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (limit,),
            )

        return [self._decode_candidate(row) for row in rows]

    async def update_candidate(
        self,
        candidate_id: str,
        *,
        skill: Skill | None = None,
        status: SkillStatus | str | None = None,
        evaluation_id: str | None = None,
        extra_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        current = await self.get_candidate(candidate_id)
        if current is None:
            raise ValueError(f"skill candidate {candidate_id!r} not found")

        metadata = dict(current["metadata"])

        if skill is not None:
            metadata["skill_bundle"] = skill.model_dump(mode="json")
            metadata["content_hash"] = skill.content_hash()

        if evaluation_id:
            metadata["evaluation_id"] = evaluation_id

        if extra_metadata:
            metadata.update(extra_metadata)

        if isinstance(status, SkillStatus):
            status_value = status.value
        else:
            status_value = status or current["status"]

        skill_value = skill or await self.get_candidate_skill(candidate_id)

        if skill_value:
            description = skill_value.description
            content = skill_value.instructions
            source_ids = skill_value.metadata.source_trajectory_ids
        else:
            description = current["description"]
            content = current["content"]
            source_ids = current["source_trajectory_ids"]

        await self._db.execute(
            """
            UPDATE skill_candidates
            SET description = ?, content = ?, status = ?,
                source_trajectory_ids = ?, updated_at = ?, metadata = ?
            WHERE id = ?
            """,
            (
                description,
                content,
                status_value,
                json.dumps(source_ids, ensure_ascii=False),
                _now(),
                json.dumps(metadata, ensure_ascii=False),
                candidate_id,
            ),
        )

        return await self.get_candidate(candidate_id) or {}

    async def find_candidate_by_hash(self, content_hash: str) -> dict[str, Any] | None:
        rows = await self._db.fetch(
            "SELECT * FROM skill_candidates ORDER BY updated_at DESC LIMIT 500"
        )
        for row in rows:
            decoded = self._decode_candidate(row)
            if decoded["metadata"].get("content_hash") == content_hash:
                return decoded
        return None

    # -------------------------------------------------
    # Evaluations
    # -------------------------------------------------

    async def create_evaluation(
        self,
        *,
        candidate_id: str,
        skill_name: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        evaluation_id = uuid.uuid4().hex

        await self._db.execute(
            """
            INSERT INTO skill_evaluations(
                id, candidate_id, skill_name, created_at, metadata
            ) VALUES(?, ?, ?, ?, ?)
            """,
            (
                evaluation_id,
                candidate_id,
                skill_name,
                _now(),
                json.dumps(metadata or {}, ensure_ascii=False),
            ),
        )

        return evaluation_id

    async def finish_evaluation(
        self,
        evaluation_id: str,
        *,
        verdict: str,
        baseline_metrics: dict[str, Any],
        candidate_metrics: dict[str, Any],
        comparison: dict[str, Any],
        case_results: list[dict[str, Any]],
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        row = await self._db.fetchone(
            "SELECT metadata FROM skill_evaluations WHERE id = ?", (evaluation_id,)
        )
        if row is None:
            raise ValueError(f"evaluation {evaluation_id!r} not found")

        merged = _loads(row.get("metadata"), {})
        if not isinstance(merged, dict):
            merged = {}
        merged.update(metadata or {})

        await self._db.execute(
            """
            UPDATE skill_evaluations
            SET completed_at = ?, verdict = ?, baseline_metrics = ?,
                candidate_metrics = ?, comparison = ?, case_results = ?, metadata = ?
            WHERE id = ?
            """,
            (
                _now(),
                verdict,
                json.dumps(baseline_metrics, ensure_ascii=False),
                json.dumps(candidate_metrics, ensure_ascii=False),
                json.dumps(comparison, ensure_ascii=False),
                json.dumps(case_results, ensure_ascii=False),
                json.dumps(merged, ensure_ascii=False),
                evaluation_id,
            ),
        )

        return await self.get_evaluation(evaluation_id) or {}

    async def get_evaluation(self, evaluation_id: str) -> dict[str, Any] | None:
        row = await self._db.fetchone(
            "SELECT * FROM skill_evaluations WHERE id = ?", (evaluation_id,)
        )
        if row is None:
            return None
        return self._decode_evaluation(row)

    async def list_evaluations(
        self,
        *,
        skill_name: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 500))

        if skill_name:
            rows = await self._db.fetch(
                """
                SELECT * FROM skill_evaluations
                WHERE skill_name = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (skill_name, limit),
            )
        else:
            rows = await self._db.fetch(
                """
                SELECT * FROM skill_evaluations
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            )

        return [self._decode_evaluation(row) for row in rows]

    # -------------------------------------------------
    # Versions
    # -------------------------------------------------

    async def add_version(
        self,
        skill: Skill,
        *,
        source_candidate_id: str | None,
        source_evaluation_id: str | None,
        status: str = "staged",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        version_id = uuid.uuid4().hex

        await self._db.execute(
            """
            INSERT INTO skill_versions(
                id, skill_name, version, status, bundle_json, content_hash,
                source_candidate_id, source_evaluation_id, created_at, metadata
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                version_id,
                skill.name,
                skill.version,
                status,
                skill.to_bundle_json(),
                skill.content_hash(),
                source_candidate_id,
                source_evaluation_id,
                _now(),
                json.dumps(metadata or {}, ensure_ascii=False),
            ),
        )

        return await self.get_version(skill.name, skill.version) or {}

    async def get_version(self, skill_name: str, version: str) -> dict[str, Any] | None:
        row = await self._db.fetchone(
            """
            SELECT * FROM skill_versions
            WHERE skill_name = ? AND version = ?
            """,
            (skill_name, version),
        )
        if row is None:
            return None
        return self._decode_version(row)

    async def get_version_skill(self, skill_name: str, version: str) -> Skill | None:
        row = await self.get_version(skill_name, version)
        if row is None:
            return None
        return Skill.from_bundle_json(row["bundle_json"])

    async def list_versions(self, skill_name: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch(
            """
            SELECT * FROM skill_versions
            WHERE skill_name = ?
            ORDER BY created_at DESC
            """,
            (skill_name,),
        )
        return [self._decode_version(row) for row in rows]

    async def set_version_status(self, version_id: str, status: str) -> None:
        await self._db.execute(
            "UPDATE skill_versions SET status = ? WHERE id = ?",
            (status, version_id),
        )

    async def set_active(
        self,
        skill: Skill,
        *,
        version_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        current = await self._db.fetchone(
            "SELECT * FROM skills WHERE id = ?", (skill.name,)
        )

        merged = _loads(current.get("metadata"), {}) if current else {}
        if not isinstance(merged, dict):
            merged = {}
        merged.update(metadata or {})
        merged["active_version_id"] = version_id
        merged["content_hash"] = skill.content_hash()

        await self._db.execute(
            """
            INSERT INTO skills(id, name, version, status, path, created_at, metadata)
            VALUES(?, ?, ?, 'active', ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                version = excluded.version,
                status = 'active',
                path = excluded.path,
                metadata = excluded.metadata
            """,
            (
                skill.name,
                skill.name,
                skill.version,
                f"/skills/{skill.name}/SKILL.md",
                _now(),
                json.dumps(merged, ensure_ascii=False),
            ),
        )

    async def get_active(self, skill_name: str) -> dict[str, Any] | None:
        row = await self._db.fetchone(
            "SELECT * FROM skills WHERE id = ?", (skill_name,)
        )
        if row is None:
            return None
        value = dict(row)
        value["metadata"] = _loads(value.get("metadata"), {})
        return value

    async def list_active(self) -> list[dict[str, Any]]:
        rows = await self._db.fetch(
            "SELECT * FROM skills WHERE status = 'active' ORDER BY name"
        )
        result = []
        for row in rows:
            value = dict(row)
            value["metadata"] = _loads(value.get("metadata"), {})
            result.append(value)
        return result

    async def list_registered(self) -> list[dict[str, Any]]:
        rows = await self._db.fetch("SELECT * FROM skills ORDER BY name")

        result = []
        for row in rows:
            value = dict(row)
            value["metadata"] = _loads(value.get("metadata"), {})
            result.append(value)
        return result

    async def set_registry_status(self, skill_name: str, status: str) -> None:
        await self._db.execute(
            "UPDATE skills SET status = ? WHERE id = ?",
            (status, skill_name),
        )

    # -------------------------------------------------
    # Decoders
    # -------------------------------------------------

    @staticmethod
    def _decode_candidate(row: dict[str, Any]) -> dict[str, Any]:
        value = dict(row)
        value["source_trajectory_ids"] = _loads(value.get("source_trajectory_ids"), [])
        value["metadata"] = _loads(value.get("metadata"), {})
        return value

    @staticmethod
    def _decode_evaluation(row: dict[str, Any]) -> dict[str, Any]:
        value = dict(row)
        for key, default in (
            ("baseline_metrics", {}),
            ("candidate_metrics", {}),
            ("comparison", {}),
            ("case_results", []),
            ("metadata", {}),
        ):
            value[key] = _loads(value.get(key), default)
        return value

    @staticmethod
    def _decode_version(row: dict[str, Any]) -> dict[str, Any]:
        value = dict(row)
        value["metadata"] = _loads(value.get("metadata"), {})
        return value
