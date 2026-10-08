"""Verified Skill Miner.

Discovers repeated successful task trajectories and converts them into
UNVERIFIED candidate skills.

Important:
    Mining != promotion.

A mined skill must still pass held-out baseline-vs-candidate evaluation
before SkillPromoter can activate it.
"""

from __future__ import annotations

import json
import re

from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
)

from pydantic import (
    BaseModel,
    Field,
)

from server.src.chat.model import (
    BifrostModelFactory,
)

from server.src.config import Settings

from server.src.guardrails.structured import (
    StructuredGuardrailError,
    StructuredOutputGuard,
)

from server.src.skills.repository import (
    SkillRepository,
)

from server.src.skills.representation.skill import (
    EvaluationMode,
    OutcomeAssertion,
    Skill,
    SkillEvalCase,
    SkillMetadata,
    SkillRisk,
    SkillStatus,
    SkillWorkflow,
    WorkflowStep,
)

from server.src.skills.trajectory_store.store import (
    TrajectoryStore,
)


# ---------------------------------------------------------------------------
# Clustering configuration
# ---------------------------------------------------------------------------

_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
    "i", "in", "is", "it", "me", "my", "of", "on", "or", "please",
    "that", "the", "this", "to", "with", "you", "your",
}

_LOCAL_MUTATION_TOOLS = {
    "write_file", "edit_file", "delete", "execute", "sqlite_execute",
    "archive_extract", "archive_create", "image_transform",
    "media_convert", "local_text_to_speech",
}

_LOCAL_STATE_DEPENDENT_TOOLS = {
    "ls", "read_file", "write_file", "edit_file", "delete", "glob",
    "grep", "execute", "document_read", "document_search",
    "document_extract", "sqlite_query", "sqlite_execute",
    "archive_extract", "archive_create", "image_transform",
    "media_convert",
}

_EXTERNAL_SIDE_EFFECT_TOOLS = {
    "browser_click", "browser_type", "browser_press", "browser_upload",
    "browser_download", "computer_click", "computer_move",
    "computer_drag", "computer_scroll", "computer_type", "computer_key",
    "computer_shortcut", "clipboard_write", "wake_on_lan",
    "process_start", "process_input", "process_stop",
    "request_approval", "schedule_create", "schedule_update",
    "schedule_delete", "task_create", "task_update", "task_complete",
    "task_delete", "memory_save", "memory_forget", "notify_user",
}

_EXTERNAL_AMBIGUOUS_TOOLS = {
    "http_request",
}


# ---------------------------------------------------------------------------
# Internal models
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class MinedTrajectory:
    trajectory_id: str
    task_id: str
    goal: str
    created_at: str
    tool_sequence: list[str]
    steps: list[dict[str, Any]]
    metadata: dict[str, Any]


@dataclass(slots=True)
class TrajectoryCluster:
    items: list[MinedTrajectory]

    @property
    def representative(self) -> MinedTrajectory:
        return max(self.items, key=lambda item: len(item.tool_sequence))

    @property
    def fingerprint(self) -> str:
        sequence = self.representative.tool_sequence
        if not sequence:
            return "reasoning-only"
        return " -> ".join(sequence)


class _SkillDraftStep(BaseModel):
    instruction: str
    tool_names: list[str] = Field(default_factory=list)
    success_signal: str | None = None


class _SkillDraft(BaseModel):
    name: str
    description: str
    instructions: str
    trigger: str
    preconditions: list[str] = Field(default_factory=list)
    steps: list[_SkillDraftStep] = Field(default_factory=list)
    success_criteria: list[str] = Field(default_factory=list)
    failure_recovery: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Miner
# ---------------------------------------------------------------------------


class SkillMiner:
    """Find repeated successful procedures.

    Flow:
        successful trajectories
                ↓
        deterministic clustering
                ↓
        mining / held-out split
                ↓
        Bifrost synthesis
                ↓
        candidate Skill
                ↓
        SkillEvaluator later decides whether it deserves promotion
    """

    def __init__(
        self,
        *,
        settings: Settings,
        trajectories: TrajectoryStore,
        repository: SkillRepository,
    ) -> None:
        self._settings = settings
        self._trajectories = trajectories
        self._repository = repository
        self._models = BifrostModelFactory(settings)
        self._structured = StructuredOutputGuard()

    async def mine(
        self,
        *,
        limit: int = 200,
        minimum_occurrences: int = 4,
        sequence_similarity: float = 0.72,
        goal_similarity: float = 0.18,
        model_name: str | None = None,
        max_candidates: int | None = None,
    ) -> list[dict[str, Any]]:
        """Mine candidate skills.

        minimum_occurrences=4 is intentional:
            2+ examples for mining
            2+ examples for held-out evaluation
        """
        successful = await self._trajectories.list_with_tasks(
            outcome="success", limit=limit
        )

        trajectories = [self._to_mined(item) for item in successful]
        trajectories = [
            item
            for item in trajectories
            if item.goal.strip() and self._eligible_for_mining(item)
        ]

        clusters = self._cluster(
            trajectories,
            sequence_similarity=sequence_similarity,
            goal_similarity=goal_similarity,
        )

        created: list[dict[str, Any]] = []

        for cluster in clusters:
            if max_candidates is not None and len(created) >= max_candidates:
                break

            if len(cluster.items) < minimum_occurrences:
                continue

            mining_items, held_out = self._split_holdout(cluster.items)

            if len(mining_items) < 2:
                continue

            if len(held_out) < 1:
                continue

            skill = await self._synthesize(
                mining_items=mining_items,
                held_out=held_out,
                fingerprint=cluster.fingerprint,
                model_name=model_name,
            )

            duplicate = await self._repository.find_candidate_by_hash(
                skill.content_hash()
            )

            if (
                duplicate is not None
                and duplicate.get("status")
                in {"candidate", "evaluating", "verified", "promoted"}
            ):
                continue

            created.append(await self._repository.create_candidate(skill))

        return created

    # ------------------------------------------------------------------
    # Trajectory extraction
    # ------------------------------------------------------------------

    @staticmethod
    def _to_mined(record: dict[str, Any]) -> MinedTrajectory:
        raw_steps = record.get("steps")
        steps = raw_steps if isinstance(raw_steps, list) else []

        tools: list[str] = []

        for step in steps:
            if not isinstance(step, dict):
                continue
            if step.get("type") != "tool.result":
                continue
            data = step.get("data")
            if not isinstance(data, dict):
                continue
            name = str(data.get("name") or "").strip()
            if not name:
                continue
            # Consecutive duplicates are frequently streaming/retry noise.
            if not tools or tools[-1] != name:
                tools.append(name)

        metadata = record.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}

        return MinedTrajectory(
            trajectory_id=str(record["id"]),
            task_id=str(record.get("task_id") or ""),
            goal=str(record.get("goal") or ""),
            created_at=str(record.get("created_at") or ""),
            tool_sequence=tools,
            steps=steps,
            metadata=metadata,
        )

    @staticmethod
    def _eligible_for_mining(item: MinedTrajectory) -> bool:
        """Exclude trajectories that are not standalone learning examples.

        A HITL continuation such as "Resume approved action for: delete old
        files" is only part of the original task and must not become a
        standalone learned skill.
        """
        if item.metadata.get("exclude_from_skill_mining") is True:
            return False
        if item.metadata.get("approval_resume") is True:
            return False
        return True

    # ------------------------------------------------------------------
    # Clustering
    # ------------------------------------------------------------------

    @classmethod
    def _cluster(
        cls,
        items: list[MinedTrajectory],
        *,
        sequence_similarity: float,
        goal_similarity: float,
    ) -> list[TrajectoryCluster]:
        clusters: list[TrajectoryCluster] = []

        # More informative workflows become cluster anchors first.
        ordered = sorted(items, key=lambda value: len(value.tool_sequence), reverse=True)

        for item in ordered:
            best_cluster: TrajectoryCluster | None = None
            best_score = -1.0

            for cluster in clusters:
                representative = cluster.representative

                sequence_score = cls._sequence_similarity(
                    item.tool_sequence, representative.tool_sequence
                )
                goal_score = cls._goal_similarity(item.goal, representative.goal)

                if sequence_score < sequence_similarity:
                    continue
                if goal_score < goal_similarity:
                    continue

                combined = 0.75 * sequence_score + 0.25 * goal_score

                if combined > best_score:
                    best_cluster = cluster
                    best_score = combined

            if best_cluster is None:
                clusters.append(TrajectoryCluster(items=[item]))
            else:
                best_cluster.items.append(item)

        return clusters

    @staticmethod
    def _sequence_similarity(left: list[str], right: list[str]) -> float:
        if not left and not right:
            return 1.0
        if not left or not right:
            return 0.0
        return SequenceMatcher(a=left, b=right, autojunk=False).ratio()

    @staticmethod
    def _goal_similarity(left: str, right: str) -> float:
        left_tokens = SkillMiner._goal_tokens(left)
        right_tokens = SkillMiner._goal_tokens(right)
        if not left_tokens or not right_tokens:
            return 0.0
        intersection = left_tokens & right_tokens
        union = left_tokens | right_tokens
        return len(intersection) / len(union)

    @staticmethod
    def _goal_tokens(value: str) -> set[str]:
        return {
            token
            for token in re.findall(r"[a-z0-9]{2,}", value.lower())
            if token not in _STOPWORDS
        }

    # ------------------------------------------------------------------
    # Train / held-out split
    # ------------------------------------------------------------------

    @staticmethod
    def _split_holdout(
        items: list[MinedTrajectory],
    ) -> tuple[list[MinedTrajectory], list[MinedTrajectory]]:
        """Temporal holdout.

        Older successful tasks teach the skill.
        More recent successful tasks test generalization.
        """
        ordered = sorted(items, key=lambda item: item.created_at)
        count = len(ordered)

        if count < 3:
            return ordered, []

        if count == 3:
            holdout_count = 1
        else:
            holdout_count = max(2, round(count / 3))
            # Always retain at least two mining examples.
            holdout_count = min(holdout_count, count - 2)

        return ordered[:-holdout_count], ordered[-holdout_count:]

    # ------------------------------------------------------------------
    # Skill synthesis
    # ------------------------------------------------------------------

    async def _synthesize(
        self,
        *,
        mining_items: list[MinedTrajectory],
        held_out: list[MinedTrajectory],
        fingerprint: str,
        model_name: str | None,
    ) -> Skill:
        chosen_model = await self._models.resolve_or_default(model_name)
        model = self._models.create(chosen_model)

        evidence: list[dict[str, Any]] = []

        for item in mining_items[:8]:
            evidence.append(
                {
                    "trajectory_id": item.trajectory_id,
                    "goal": item.goal[:4000],
                    "tool_sequence": item.tool_sequence,
                    "events": self._compact_events(item.steps),
                }
            )

        payload = {
            "objective": (
                "Create one reusable procedural skill from repeated "
                "successful Trajecta personal-agent executions."
            ),
            "rules": [
                "Return JSON only.",
                "Do not invent credentials, paths, APIs, user facts, or tool capabilities.",
                "Do not expose hidden chain-of-thought.",
                "Write concise operational instructions.",
                "Preserve user approvals and safety boundaries.",
                "Never instruct the agent to bypass permissions.",
                "Only reference tools observed in the evidence.",
                "Include explicit verification before claiming success.",
                "Include failure recovery instructions.",
            ],
            "repeated_pattern": fingerprint,
            "evidence": evidence,
            "schema": {
                "name": "lowercase-kebab-case",
                "description": "what this procedure does and when it should activate",
                "instructions": "concise markdown instructions",
                "trigger": "when the skill should be used",
                "preconditions": ["string"],
                "steps": [
                    {
                        "instruction": "string",
                        "tool_names": ["string"],
                        "success_signal": "string or null",
                    }
                ],
                "success_criteria": ["string"],
                "failure_recovery": ["string"],
            },
        }

        messages = [
            SystemMessage(
                content=(
                    "Return exactly one valid JSON object matching "
                    "the requested CandidateSkill schema. "
                    "Do not include prose or markdown fences."
                )
            ),
            HumanMessage(
                content=json.dumps(payload, ensure_ascii=False)
            ),
        ]

        draft: _SkillDraft | None = None
        last_error: StructuredGuardrailError | None = None

        for attempt in range(2):
            response = await model.ainvoke(messages)
            raw = self._message_text(response.content)

            try:
                draft = await self._structured.validate_pydantic(raw, _SkillDraft)
                break
            except StructuredGuardrailError as exc:
                last_error = exc

                if attempt == 1:
                    raise

                # The repair call goes through the SAME Bifrost model,
                # not Guardrails or OpenAI directly.
                messages.extend(
                    [
                        AIMessage(content=raw),
                        HumanMessage(
                            content=(
                                "The previous response failed the "
                                "JSON/schema contract: "
                                f"{exc}. "
                                "Return exactly one JSON object "
                                "matching the requested schema, "
                                "with no prose or markdown fences."
                            )
                        ),
                    ]
                )

        if draft is None:
            raise (
                last_error
                or StructuredGuardrailError(
                    "skill synthesizer did not return "
                    "a valid structured response"
                )
            )

        observed_tools = sorted(
            {
                tool_name
                for trajectory in [*mining_items, *held_out]
                for tool_name in trajectory.tool_sequence
            }
        )

        risk = self._classify_risk(observed_tools)

        eval_cases: list[SkillEvalCase] = []

        for index, trajectory in enumerate(held_out, start=1):
            mode = self._evaluation_mode(trajectory, risk=risk)

            item_tools = set(trajectory.tool_sequence)
            needs_fixture = bool(item_tools & _LOCAL_STATE_DEPENDENT_TOOLS)
            fixture_complete = (
                trajectory.metadata.get("replay_fixture_complete")
                is True
            )
            fixture_id = (
                trajectory.metadata.get("replay_fixture_id")
                if needs_fixture and fixture_complete
                else None
            )

            raw_assertions = trajectory.metadata.get("outcome_assertions")
            if not isinstance(raw_assertions, list):
                raw_assertions = []

            outcome_assertions: list[OutcomeAssertion] = []
            for raw_assertion in raw_assertions:
                if not isinstance(raw_assertion, dict):
                    continue
                try:
                    outcome_assertions.append(
                        OutcomeAssertion.model_validate(raw_assertion)
                    )
                except ValueError:
                    continue

            outcome_complete = bool(
                trajectory.metadata.get("outcome_capture_complete")
            )

            eval_cases.append(
                SkillEvalCase(
                    id=f"heldout-{index:03d}",
                    name=f"Held-out trajectory {index}",
                    task=trajectory.goal,
                    rubric=[
                        "Complete the user's task correctly.",
                        "Respect permission and safety boundaries.",
                        "Use tools appropriately.",
                        "Avoid unnecessary loops.",
                        "Verify the result before claiming success.",
                    ],
                    allowed_tools=sorted(set(trajectory.tool_sequence)),
                    outcome_assertions=outcome_assertions,
                    mode=mode,
                    source_trajectory_id=trajectory.trajectory_id,
                    metadata={
                        "source_task_id": trajectory.task_id,
                        "source_created_at": trajectory.created_at,
                        "requires_replay_fixture": needs_fixture,
                        "replay_fixture_id": fixture_id,
                        "replay_fixture_complete": fixture_complete,
                        "outcome_capture_complete": outcome_complete,
                        "outcome_assertion_count": len(outcome_assertions),
                    },
                )
            )

        return Skill(
            name=self._safe_name(draft.name),
            description=draft.description.strip(),
            instructions=draft.instructions.strip(),
            status=SkillStatus.CANDIDATE,
            workflow=SkillWorkflow(
                trigger=draft.trigger.strip(),
                preconditions=[
                    value.strip()
                    for value in draft.preconditions
                    if value.strip()
                ],
                steps=[
                    WorkflowStep(
                        instruction=step.instruction.strip(),
                        tool_names=[
                            tool_name
                            for tool_name in step.tool_names
                            if tool_name in observed_tools
                        ],
                        success_signal=(
                            (step.success_signal or "").strip() or None
                        ),
                    )
                    for step in draft.steps
                    if step.instruction.strip()
                ],
                success_criteria=[
                    value.strip()
                    for value in draft.success_criteria
                    if value.strip()
                ],
                failure_recovery=[
                    value.strip()
                    for value in draft.failure_recovery
                    if value.strip()
                ],
            ),
            eval_cases=eval_cases,
            metadata=SkillMetadata(
                source_trajectory_ids=[
                    item.trajectory_id for item in mining_items
                ],
                held_out_trajectory_ids=[
                    item.trajectory_id for item in held_out
                ],
                risk=risk,
                mining_fingerprint=fingerprint,
                model_name=chosen_model,
                notes={
                    "observed_tools": observed_tools,
                    "source_goal_keywords": dict(
                        Counter(
                            token
                            for item in mining_items
                            for token in self._goal_tokens(item.goal)
                        ).most_common(20)
                    ),
                },
            ),
        )

    # ------------------------------------------------------------------
    # Evaluation safety classification
    # ------------------------------------------------------------------

    @staticmethod
    def _classify_risk(tools: list[str]) -> SkillRisk:
        names = set(tools)

        if names & (_EXTERNAL_SIDE_EFFECT_TOOLS | _EXTERNAL_AMBIGUOUS_TOOLS):
            return SkillRisk.EXTERNAL_SIDE_EFFECT

        if names & _LOCAL_MUTATION_TOOLS:
            return SkillRisk.SANDBOXED

        return SkillRisk.READ_ONLY

    @staticmethod
    def _evaluation_mode(
        trajectory: MinedTrajectory,
        *,
        risk: SkillRisk,
    ) -> EvaluationMode:
        if risk == SkillRisk.EXTERNAL_SIDE_EFFECT:
            return EvaluationMode.MANUAL

        tools = set(trajectory.tool_sequence)

        if tools & _LOCAL_STATE_DEPENDENT_TOOLS:
            # For state-changing local work, automatic evaluation is only
            # trustworthy when we have both:
            #
            # 1. exact initial state
            # 2. observable successful outcome
            fixture_id = trajectory.metadata.get("replay_fixture_id")
            fixture_complete = trajectory.metadata.get("replay_fixture_complete")
            outcome_complete = trajectory.metadata.get("outcome_capture_complete")
            outcome_assertions = trajectory.metadata.get("outcome_assertions")

            if not (fixture_id and fixture_complete):
                return EvaluationMode.MANUAL
            if not (outcome_complete and outcome_assertions):
                return EvaluationMode.MANUAL

        return EvaluationMode.SANDBOX

    # ------------------------------------------------------------------
    # Evidence compression
    # ------------------------------------------------------------------

    @staticmethod
    def _compact_events(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []

        for step in steps:
            if not isinstance(step, dict):
                continue

            event_type = str(step.get("type") or "")

            if event_type not in {"tool.call.delta", "tool.result", "agent.step"}:
                continue

            data = step.get("data")
            if not isinstance(data, dict):
                data = {}

            compact: dict[str, Any] = {
                "type": event_type,
                "source": step.get("source"),
            }

            if event_type == "tool.result":
                compact.update(
                    {
                        "name": data.get("name"),
                        "status": data.get("status"),
                        "content": str(data.get("content") or "")[:1000],
                    }
                )
            elif event_type == "tool.call.delta":
                compact.update(
                    {
                        "name": data.get("name"),
                        "arguments": data.get("arguments"),
                    }
                )
            else:
                compact["data"] = {
                    key: value
                    for key, value in data.items()
                    if key not in {"reasoning", "chain_of_thought", "thinking"}
                }

            result.append(compact)

            if len(result) >= 30:
                break

        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _safe_name(value: str) -> str:
        name = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
        name = re.sub(r"-+", "-", name)
        name = name[:64].strip("-")
        return name or "learned-procedure"

    @staticmethod
    def _message_text(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            result: list[str] = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                text = block.get("text")
                if isinstance(text, str):
                    result.append(text)
            return "".join(result)
        return str(content)
