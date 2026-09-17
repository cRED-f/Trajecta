"""Verified skill promotion and rollback."""

from __future__ import annotations

from server.src.skills.repository import SkillRepository
from server.src.skills.representation.skill import SkillStatus
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

        result = await self._versioner.promote_new_version(
            skill,
            candidate_id=candidate_id,
            evaluation_id=str(evaluation_id),
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