"""Skills service — wires mining/evaluation/promotion persistence for the API.

Aggregates the SkillRepository, replay fixtures, replay executor, evaluator,
versioner and promoter into one object the REST routes can reach through
``request.app.state.skills_service``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from server.src.config import Settings
from server.src.memory.procedural.store import ProceduralMemory
from server.src.skills.evaluation.evaluator import SkillEvaluator
from server.src.skills.evaluation.fixtures import ReplayFixtureStore
from server.src.skills.evaluation.replay import DeepAgentReplayExecutor, SkillReplay
from server.src.skills.promotion.promoter import SkillPromoter
from server.src.skills.repository import SkillRepository
from server.src.skills.versioning.versioner import SkillVersioner

if TYPE_CHECKING:
    from server.src.memory.provider import MemoryProvider
    from server.src.tools.personal import PersonalToolProvider


class SkillsService:
    def __init__(
        self,
        settings: Settings,
        memory: "MemoryProvider",
        personal_tools: "PersonalToolProvider",
    ) -> None:
        if memory.sqlite is None:
            raise RuntimeError("MemoryProvider must be opened before SkillsService")

        repository = SkillRepository(memory.sqlite)

        replay_fixtures = ReplayFixtureStore(settings, memory.sqlite)

        executor = DeepAgentReplayExecutor(
            settings=settings,
            memory=memory,
            personal_tools=personal_tools,
            replay_fixtures=replay_fixtures,
        )
        replay = SkillReplay(executor)

        self.repository = repository
        self.replay_fixtures = replay_fixtures
        self.evaluator = SkillEvaluator(repository=repository, replay=replay)

        self.versioner = SkillVersioner(
            repository=repository,
            procedural=ProceduralMemory(memory),
        )
        self.promoter = SkillPromoter(
            repository=repository,
            versioner=self.versioner,
        )

    async def reject_candidate(
        self,
        candidate_id: str,
        *,
        reason: str,
    ) -> dict[str, Any]:
        return await self.promoter.reject(candidate_id, reason=reason)


def build_skills_service(
    settings: Settings,
    memory: "MemoryProvider",
    personal_tools: "PersonalToolProvider",
) -> SkillsService:
    """Construct the skills service used by the API and keep it on app.state."""
    return SkillsService(settings, memory, personal_tools)