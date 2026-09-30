"""Verified skill promotion and rollback."""

from __future__ import annotations

from server.src.skills.repository import SkillRepository
from server.src.skills.representation.skill import Skill, SkillStatus
from server.src.skills.versioning.versioner import PromotionResult, SkillVersioner


class SkillPromoter:
    """
    A skill can ONLY be promoted when a persisted evaluation has
    verdict == pass.
    """

    def __init__(
        self,
        *,
        repository: SkillRepository,
        versioner: SkillVersioner,
    ) -> None:
        self._repository = repository
        self._versioner = versioner

    async def promote(
        self,
        *,
        candidate_id: str,
        evaluation_id: str | None = None,
    ) -> PromotionResult:
        skill, evaluation_id = await self._require_verified(candidate_id, evaluation_id)

        result = await self._versioner.promote_new_version(
            skill,
            candidate_id=candidate_id,
            evaluation_id=evaluation_id,
        )

        await self._repository.update_candidate(
            candidate_id,
            status="promoted",
            extra_metadata={
                "promoted_version": result.version,
                "promoted_version_id": result.version_id,
                "previous_version": result.previous_version,
            },
        )

        return result

    async def stage(
        self,
        *,
        candidate_id: str,
        evaluation_id: str | None = None,
    ) -> PromotionResult:
        """Mint the next version row for a verified candidate, without activating it.

        The version exists so an experiment can arm against it, but
        nothing is written to /skills/ — the active version keeps serving
        until a winner is picked.
        """

        skill, evaluation_id = await self._require_verified(candidate_id, evaluation_id)

        return await self._versioner.stage_new_version(
            skill,
            candidate_id=candidate_id,
            evaluation_id=evaluation_id,
        )

    async def _require_verified(
        self,
        candidate_id: str,
        evaluation_id: str | None = None,
    ) -> tuple[Skill, str]:
        """Resolve a candidate to (skill, evaluation_id) or explain why it cannot go.

        Promotion and staging share this gate: neither may mint a version
        from a candidate the evaluator has not passed.
        """

        candidate = await self._repository.get_candidate(candidate_id)
        skill = await self._repository.get_candidate_skill(candidate_id)

        if candidate is None or skill is None:
            raise ValueError(f"skill candidate {candidate_id!r} not found")

        if candidate.get("status") == "promoted":
            raise ValueError("candidate has already been promoted")

        evaluation_id = evaluation_id or candidate["metadata"].get("evaluation_id")

        if not evaluation_id:
            raise ValueError("candidate has not been evaluated")

        evaluation = await self._repository.get_evaluation(str(evaluation_id))

        if evaluation is None:
            raise ValueError(f"evaluation {evaluation_id!r} not found")

        if evaluation.get("candidate_id") != candidate_id:
            raise ValueError("evaluation does not belong to this candidate")

        if evaluation.get("verdict") != "pass":
            raise ValueError(
                "candidate cannot be promoted; "
                f"evaluation verdict is {evaluation.get('verdict')!r}"
            )

        if candidate.get("status") not in {
            "verified",
            SkillStatus.VERIFIED.value,
        }:
            raise ValueError(
                f"candidate status is {candidate.get('status')!r}; expected 'verified'"
            )

        return skill, str(evaluation_id)

    async def reject(
        self,
        candidate_id: str,
        *,
        reason: str,
    ) -> dict:
        reason = reason.strip()

        if not reason:
            raise ValueError("rejection reason cannot be empty")

        candidate = await self._repository.get_candidate(candidate_id)

        if candidate is None:
            raise ValueError(f"skill candidate {candidate_id!r} not found")

        if candidate.get("status") == "promoted":
            raise ValueError("an already-promoted candidate cannot be rejected")

        return await self._repository.update_candidate(
            candidate_id,
            status=SkillStatus.REJECTED,
            extra_metadata={"rejection_reason": reason},
        )

    async def rollback(
        self,
        skill_name: str,
        version: str,
    ) -> PromotionResult:
        return await self._versioner.rollback(skill_name, version)

    async def rollback_to_version(
        self,
        *,
        skill_name: str,
        version: str,
        reason: str | None = None,
    ) -> dict:
        """Restore a previous version and report what moved where.

        Delegates to SkillVersioner.rollback so the restored bundle is
        re-materialized into /skills/<name>/ — flipping the DB status alone
        would leave the running agent on the new version.
        """

        current = await self._repository.get_active(skill_name)

        if current is None:
            raise ValueError("Skill has no active version")

        target = await self._repository.get_version(skill_name, version)

        if target is None:
            raise ValueError(f"Version {version} not found")

        if str(current["version"]) == version:
            raise ValueError(f"Version {version} is already active")

        result = await self._versioner.rollback(
            skill_name,
            version,
            reason=reason,
        )

        return {
            "skill": skill_name,
            "rolled_back_from": result.previous_version,
            "rolled_back_to": result.version,
            "reason": reason,
        }