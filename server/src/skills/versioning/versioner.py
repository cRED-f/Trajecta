"""Immutable skill versioning, staging, activation and rollback.

A verified candidate is *staged* first: the version row is written but
nothing is materialized into ``/skills/``, so an experiment can compare it
against the active version before anyone's agent sees it. Activation is
the explicit step that materializes a version, and it refuses to run when
the skill's required dependencies are unsatisfied.
"""

from __future__ import annotations

from dataclasses import dataclass

from typing import TYPE_CHECKING, Any

from server.src.memory.procedural.store import ProceduralMemory
from server.src.skills.repository import SkillRepository
from server.src.skills.representation.skill import Skill, SkillStatus

if TYPE_CHECKING:
    from server.src.skills.dependencies.graph import SkillDependencyGraph


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
        dependencies: "SkillDependencyGraph | None" = None,
    ) -> None:
        self._repository = repository
        self._procedural = procedural
        self._dependencies = dependencies

    async def stage_new_version(
        self,
        skill: Skill,
        *,
        candidate_id: str,
        evaluation_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> PromotionResult:
        """Write an immutable version row without materializing it.

        This is the whole point of staging: the candidate gets a real
        version number that an experiment can arm against, while
        ``/skills/<name>/`` keeps serving whatever is active today.
        """

        active = await self._repository.get_active(skill.name)
        previous_version = str(active["version"]) if active else None

        next_version = await self._next_version(skill.name)

        staged = skill.model_copy(
            update={
                "version": next_version,
                "status": SkillStatus.VERIFIED,
            }
        )

        snapshot = await self._repository.add_version(
            staged,
            source_candidate_id=candidate_id,
            source_evaluation_id=evaluation_id,
            status="staged",
            metadata={"previous_version": previous_version, **(metadata or {})},
        )

        return PromotionResult(
            skill_name=staged.name,
            version=staged.version,
            version_id=str(snapshot["id"]),
            previous_version=previous_version,
        )

    async def promote_new_version(
        self,
        skill: Skill,
        *,
        candidate_id: str,
        evaluation_id: str,
    ) -> PromotionResult:
        """Stage the candidate, then activate it straight away.

        Used when there is no experiment to run: the first version of a
        skill, or an upgrade the learning loop makes without evidence to
        weigh.
        """

        staged = await self.stage_new_version(
            skill,
            candidate_id=candidate_id,
            evaluation_id=evaluation_id,
        )

        return await self.activate_version(
            staged.skill_name,
            staged.version,
            metadata={
                "source_candidate_id": candidate_id,
                "source_evaluation_id": evaluation_id,
            },
        )

    async def activate_version(
        self,
        skill_name: str,
        version: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> PromotionResult:
        """Materialize a version into /skills/ and make it the active one.

        Refuses to run while a required dependency is inactive or outside
        its constraint — activating a skill the agent cannot actually use
        would silently break every run that loads it.
        """

        target_record = await self._repository.get_version(skill_name, version)
        target_skill = await self._repository.get_version_skill(skill_name, version)

        if target_record is None or target_skill is None:
            raise ValueError(f"skill version {skill_name}@{version} not found")

        if self._dependencies is not None:
            errors = await self._dependencies.validate_activation(skill_name)

            if errors:
                raise ValueError(
                    "skill dependencies are not satisfied: " + "; ".join(errors)
                )

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

        restored = target_skill.model_copy(update={"status": SkillStatus.ACTIVE})

        try:
            await self._procedural.apromote_bundle(
                restored.name,
                restored.bundle_files(),
            )
        except Exception:
            # A half-written bundle is worse than none: put the previous
            # version back before reporting the failure.
            if current_skill is not None:
                try:
                    await self._procedural.apromote_bundle(
                        current_skill.name,
                        current_skill.bundle_files(),
                    )
                except Exception:
                    pass

            await self._repository.set_version_status(
                str(target_record["id"]), "activation_failed"
            )
            raise

        if current_version_id and str(current_version_id) != str(target_record["id"]):
            await self._repository.set_version_status(
                str(current_version_id), "archived"
            )

        await self._repository.set_version_status(str(target_record["id"]), "active")

        await self._repository.set_active(
            restored,
            version_id=str(target_record["id"]),
            metadata={"previous_version": current_version, **(metadata or {})},
        )

        return PromotionResult(
            skill_name=skill_name,
            version=version,
            version_id=str(target_record["id"]),
            previous_version=current_version,
        )

    async def rollback(
        self,
        skill_name: str,
        version: str,
        *,
        reason: str | None = None,
    ) -> PromotionResult:
        """Make an older version active again, re-materializing its bundle."""

        return await self.activate_version(
            skill_name,
            version,
            metadata={
                "rollback": True,
                "rolled_back_from": await self._current_version(skill_name),
                "rollback_reason": reason,
            },
        )

    async def archive_version(self, skill_name: str, version: str) -> None:
        record = await self._repository.get_version(skill_name, version)

        if record is None:
            raise ValueError(f"skill version {skill_name}@{version} not found")

        active = await self._repository.get_active(skill_name)

        if active and str(active.get("version")) == version:
            raise ValueError("cannot archive the active skill version")

        await self._repository.set_version_status(str(record["id"]), "archived")

    async def list_versions(self, skill_name: str) -> list[dict]:
        """Return the full historical version list for a skill, newest first."""

        versions = await self._repository.list_versions(skill_name)

        return [
            {
                "version": item["version"],
                "status": item["status"],
                "created_at": item["created_at"],
                "metadata": item["metadata"],
            }
            for item in versions
        ]

    async def get_active_version(self, skill_name: str) -> dict | None:
        """Return the currently active version of a skill, if any."""

        version = await self._repository.get_active(skill_name)

        if version is None:
            return None

        return {
            "version": version["version"],
            "status": version["status"],
            "metadata": version["metadata"],
        }

    async def compare_versions(
        self, skill_name: str, old_version: str, new_version: str
    ) -> dict:
        """Diff two stored versions of the same skill."""

        old = await self._repository.get_version(skill_name, old_version)
        new = await self._repository.get_version(skill_name, new_version)

        if old is None:
            raise ValueError(f"Unknown version {old_version}")
        if new is None:
            raise ValueError(f"Unknown version {new_version}")

        return {
            "skill": skill_name,
            "from": {"version": old["version"], "metadata": old["metadata"]},
            "to": {"version": new["version"], "metadata": new["metadata"]},
            # content_hash covers the whole bundle, so it is the cheap and
            # authoritative "did anything actually change" signal.
            "changed": old["content_hash"] != new["content_hash"],
        }

    async def _current_version(self, skill_name: str) -> str | None:
        current = await self._repository.get_active(skill_name)

        return str(current["version"]) if current else None

    async def _next_version(self, skill_name: str) -> str:
        """Patch bump past the highest stored version, not just the active one.

        Staging two candidates for one skill must not collide, and basing
        this on the active version would hand both the same number.
        """

        versions = await self._repository.list_versions(skill_name)

        parsed: list[tuple[int, int, int]] = []

        for item in versions:
            try:
                parsed.append(self._parse_version(str(item["version"])))
            except ValueError:
                continue

        if not parsed:
            active = await self._repository.get_active(skill_name)

            if active:
                try:
                    parsed.append(self._parse_version(str(active["version"])))
                except ValueError:
                    pass

        if not parsed:
            return "1.0.0"

        major, minor, patch = max(parsed)

        return f"{major}.{minor}.{patch + 1}"

    @staticmethod
    def _parse_version(value: str) -> tuple[int, int, int]:
        parts = value.split(".")
        if len(parts) != 3 or any(not part.isdigit() for part in parts):
            raise ValueError(f"invalid stored skill version: {value!r}")

        return (int(parts[0]), int(parts[1]), int(parts[2]))
