"""Immutable skill versioning, activation and rollback."""

from __future__ import annotations

from dataclasses import dataclass

from server.src.memory.procedural.store import ProceduralMemory
from server.src.skills.repository import SkillRepository
from server.src.skills.representation.skill import Skill, SkillStatus


@dataclass(slots=True)
class PromotionResult:
    skill_name: str
    version: str
    version_id: str
    previous_version: str | None = None


class SkillVersioner:
    """
    SQLite owns immutable history.

    /skills/<name>/ is only the currently active Deep Agents
    materialized skill bundle.
    """

    def __init__(
        self,
        *,
        repository: SkillRepository,
        procedural: ProceduralMemory,
    ) -> None:
        self._repository = repository
        self._procedural = procedural

    async def promote_new_version(
        self,
        skill: Skill,
        *,
        candidate_id: str,
        evaluation_id: str,
    ) -> PromotionResult:
        active = await self._repository.get_active(skill.name)
        previous_version = str(active["version"]) if active else None
        previous_version_id = None
        previous_skill: Skill | None = None

        if active:
            previous_version_id = active.get("metadata", {}).get("active_version_id")
            if previous_version:
                previous_skill = await self._repository.get_version_skill(
                    skill.name, previous_version
                )

        next_version = await self._next_version(skill.name)

        promoted = skill.model_copy(
            update={
                "version": next_version,
                "status": SkillStatus.ACTIVE,
            }
        )

        # Immutable DB snapshot first.
        snapshot = await self._repository.add_version(
            promoted,
            source_candidate_id=candidate_id,
            source_evaluation_id=evaluation_id,
            status="staged",
            metadata={"previous_version": previous_version},
        )

        version_id = str(snapshot["id"])

        # Then materialize runtime copy.
        try:
            await self._procedural.apromote_bundle(
                promoted.name,
                promoted.bundle_files(),
            )
        except Exception:
            await self._repository.set_version_status(version_id, "activation_failed")

            # Restore previous active version if replacement failed halfway.
            if previous_skill is not None:
                try:
                    await self._procedural.apromote_bundle(
                        previous_skill.name,
                        previous_skill.bundle_files(),
                    )
                except Exception:
                    pass

            raise

        if previous_version_id:
            await self._repository.set_version_status(
                str(previous_version_id), "archived"
            )

        await self._repository.set_version_status(version_id, "active")

        await self._repository.set_active(
            promoted,
            version_id=version_id,
            metadata={
                "source_candidate_id": candidate_id,
                "source_evaluation_id": evaluation_id,
                "previous_version": previous_version,
            },
        )

        return PromotionResult(
            skill_name=promoted.name,
            version=promoted.version,
            version_id=version_id,
            previous_version=previous_version,
        )

    async def rollback(
        self,
        skill_name: str,
        version: str,
    ) -> PromotionResult:
        target_record = await self._repository.get_version(skill_name, version)
        target_skill = await self._repository.get_version_skill(skill_name, version)

        if target_record is None or target_skill is None:
            raise ValueError(f"skill version {skill_name}@{version} not found")

        current = await self._repository.get_active(skill_name)
        current_version = str(current["version"]) if current else None
        current_version_id = None
        current_skill: Skill | None = None

        if current:
            current_version_id = current.get("metadata", {}).get("active_version_id")
            if current_version:
                current_skill = await self._repository.get_version_skill(
                    skill_name, current_version
                )

        restored = target_skill.model_copy(
            update={"status": SkillStatus.ACTIVE}
        )

        try:
            await self._procedural.apromote_bundle(
                restored.name,
                restored.bundle_files(),
            )
        except Exception:
            if current_skill is not None:
                try:
                    await self._procedural.apromote_bundle(
                        current_skill.name,
                        current_skill.bundle_files(),
                    )
                except Exception:
                    pass

            raise

        if current_version_id and str(current_version_id) != str(target_record["id"]):
            await self._repository.set_version_status(
                str(current_version_id), "archived"
            )

        await self._repository.set_version_status(str(target_record["id"]), "active")

        await self._repository.set_active(
            restored,
            version_id=str(target_record["id"]),
            metadata={
                "rollback": True,
                "rolled_back_from": current_version,
            },
        )

        return PromotionResult(
            skill_name=skill_name,
            version=version,
            version_id=str(target_record["id"]),
            previous_version=current_version,
        )

    async def _next_version(self, skill_name: str) -> str:
        active = await self._repository.get_active(skill_name)

        if active is None:
            return "1.0.0"

        major, minor, patch = self._parse_version(str(active["version"]))
        return f"{major}.{minor}.{patch + 1}"

    @staticmethod
    def _parse_version(value: str) -> tuple[int, int, int]:
        parts = value.split(".")
        if len(parts) != 3 or any(not part.isdigit() for part in parts):
            raise ValueError(f"invalid stored skill version: {value!r}")
        return int(parts[0]), int(parts[1]), int(parts[2])