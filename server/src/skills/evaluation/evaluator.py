"""Verified Skill evaluator.

Promotion is gated, not decided by one opaque weighted score.

Order:

    safety
        ↓
    reliability
        ↓
    generalization
        ↓
    improvement
        ↓
    PASS / FAIL
"""

from __future__ import annotations

from statistics import mean

from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
)

from server.src.skills.evaluation.replay import (
    ReplayResult,
    SkillReplay,
)

from server.src.skills.repository import (
    SkillRepository,
)

from server.src.skills.representation.skill import (
    Skill,
    SkillStatus,
)


# ---------------------------------------------------------------------------
# Aggregate models
# ---------------------------------------------------------------------------


class AggregateMetrics(
    BaseModel
):
    model_config = ConfigDict(
        extra="forbid"
    )

    total_cases: int = 0

    total_runs: int = 0

    graded_cases: int = 0

    graded_runs: int = 0

    skipped_runs: int = 0

    successes: int = 0

    success_rate: float = 0.0

    average_score: float = 0.0

    average_duration_seconds: (
        float
    ) = 0.0

    average_tool_calls: (
        float
    ) = 0.0

    average_total_tokens: (
        float
    ) = 0.0

    average_retries: (
        float
    ) = 0.0

    tool_errors: int = 0

    safety_violations: int = 0

    interrupted_runs: int = 0

    verification_available_runs: (
        int
    ) = 0

    verification_decisive_runs: (
        int
    ) = 0

    verification_failed_runs: (
        int
    ) = 0

    verification_pass_rate: (
        float
    ) = 0.0


class SkillComparison(
    BaseModel
):
    model_config = ConfigDict(
        extra="forbid"
    )

    success_rate_delta: (
        float
    ) = 0.0

    score_delta: float = 0.0

    token_improvement: (
        float
    ) = 0.0

    tool_call_improvement: (
        float
    ) = 0.0

    duration_improvement: (
        float
    ) = 0.0

    retry_improvement: (
        float
    ) = 0.0

    efficiency_gain: (
        float
    ) = 0.0

    safety_pass: bool = False

    state_verification_pass: (
        bool
    ) = False

    reliability_pass: bool = False

    generalization_pass: (
        bool
    ) = False

    improvement_pass: (
        bool
    ) = False

    reasons: list[str] = (
        Field(
            default_factory=list
        )
    )


class SkillEvaluationReport(
    BaseModel
):
    model_config = ConfigDict(
        extra="forbid"
    )

    id: str

    candidate_id: str

    skill_name: str

    verdict: str

    baseline: AggregateMetrics

    candidate: AggregateMetrics

    comparison: SkillComparison

    case_results: list[
        dict[str, Any]
    ] = Field(
        default_factory=list
    )


# ---------------------------------------------------------------------------
# Evaluator
# ---------------------------------------------------------------------------


class SkillEvaluator:
    """
    Evaluate candidate skill against baseline.

    Promotion formula:

        SafetyPass
        AND ReliabilityPass
        AND GeneralizationPass
        AND ImprovementPass
    """

    def __init__(
        self,
        *,
        repository: SkillRepository,
        replay: SkillReplay,
    ) -> None:
        self._repository = (
            repository
        )

        self._replay = (
            replay
        )

    async def evaluate(
        self,
        candidate_id: str,
        *,
        model_name: (
            str | None
        ) = None,
    ) -> SkillEvaluationReport:
        skill = (
            await self
            ._repository
            .get_candidate_skill(
                candidate_id
            )
        )

        if skill is None:
            raise ValueError(
                "skill candidate "
                f"{candidate_id!r} "
                "not found"
            )

        if not skill.eval_cases:
            raise ValueError(
                "candidate has no "
                "evaluation cases"
            )

        await self._repository.update_candidate(
            candidate_id,

            status=(
                SkillStatus
                .EVALUATING
            ),
        )

        evaluation_id = (
            await self
            ._repository
            .create_evaluation(
                candidate_id=(
                    candidate_id
                ),

                skill_name=(
                    skill.name
                ),

                metadata={
                    "model_name":
                        model_name,

                    "repetitions_per_case":
                        skill
                        .eval_config
                        .repetitions_per_case,
                },
            )
        )

        pairs: list[
            tuple[
                ReplayResult,
                ReplayResult,
            ]
        ] = []

        try:
            for case in (
                skill.eval_cases
            ):
                case_pairs = (
                    await self
                    ._replay
                    .replay_case(
                        skill=skill,

                        case=case,

                        repetitions=(
                            skill
                            .eval_config
                            .repetitions_per_case
                        ),

                        model_name=(
                            model_name
                        ),
                    )
                )

                pairs.extend(
                    case_pairs
                )

            baseline_results = [
                baseline
                for (
                    baseline,
                    _candidate,
                )
                in pairs
            ]

            candidate_results = [
                candidate
                for (
                    _baseline,
                    candidate,
                )
                in pairs
            ]

            baseline_metrics = (
                self._aggregate(
                    baseline_results
                )
            )

            candidate_metrics = (
                self._aggregate(
                    candidate_results
                )
            )

            comparison = (
                self._compare(
                    skill,
                    baseline_metrics,
                    candidate_metrics,
                )
            )

            verdict = (
                self._verdict(
                    comparison,
                    baseline_metrics,
                    candidate_metrics,
                )
            )

            case_results = [
                {
                    "case_id":
                        baseline.case_id,

                    "repetition":
                        baseline.repetition,

                    "baseline":
                        baseline.model_dump(
                            mode="json"
                        ),

                    "candidate":
                        candidate.model_dump(
                            mode="json"
                        ),
                }
                for (
                    baseline,
                    candidate,
                )
                in pairs
            ]

            report = (
                SkillEvaluationReport(
                    id=evaluation_id,

                    candidate_id=(
                        candidate_id
                    ),

                    skill_name=(
                        skill.name
                    ),

                    verdict=(
                        verdict
                    ),

                    baseline=(
                        baseline_metrics
                    ),

                    candidate=(
                        candidate_metrics
                    ),

                    comparison=(
                        comparison
                    ),

                    case_results=(
                        case_results
                    ),
                )
            )

            await (
                self
                ._repository
                .finish_evaluation(
                    evaluation_id,

                    verdict=verdict,

                    baseline_metrics=(
                        baseline_metrics
                        .model_dump(
                            mode="json"
                        )
                    ),

                    candidate_metrics=(
                        candidate_metrics
                        .model_dump(
                            mode="json"
                        )
                    ),

                    comparison=(
                        comparison
                        .model_dump(
                            mode="json"
                        )
                    ),

                    case_results=(
                        case_results
                    ),
                )
            )

            if verdict == "pass":
                candidate_status = (
                    SkillStatus
                    .VERIFIED
                )

            elif verdict == "fail":
                candidate_status = (
                    SkillStatus
                    .REJECTED
                )

            else:
                # Manual/skipped/insufficient evidence should not be
                # incorrectly labelled rejected.
                candidate_status = (
                    SkillStatus
                    .CANDIDATE
                )

            await (
                self
                ._repository
                .update_candidate(
                    candidate_id,

                    status=(
                        candidate_status
                    ),

                    evaluation_id=(
                        evaluation_id
                    ),

                    extra_metadata={
                        "evaluation_verdict":
                            verdict,
                    },
                )
            )

            return report

        except Exception as exc:
            await (
                self
                ._repository
                .finish_evaluation(
                    evaluation_id,

                    verdict="error",

                    baseline_metrics={},

                    candidate_metrics={},

                    comparison={},

                    case_results=[],

                    metadata={
                        "error": (
                            f"{type(exc).__name__}: "
                            f"{exc}"
                        )
                    },
                )
            )

            await (
                self
                ._repository
                .update_candidate(
                    candidate_id,

                    status=(
                        SkillStatus
                        .CANDIDATE
                    ),

                    evaluation_id=(
                        evaluation_id
                    ),

                    extra_metadata={
                        "evaluation_error": (
                            f"{type(exc).__name__}: "
                            f"{exc}"
                        )
                    },
                )
            )

            raise

    # ------------------------------------------------------------------
    # Aggregate
    # ------------------------------------------------------------------

    @staticmethod
    def _aggregate(
        results: list[
            ReplayResult
        ],
    ) -> AggregateMetrics:
        case_ids = {
            result.case_id
            for result
            in results
        }

        graded = [
            result
            for result
            in results
            if (
                result.success
                is not None
                and not result.skipped
            )
        ]

        completed = [
            result
            for result
            in results
            if not result.skipped
        ]

        graded_case_ids = {
            result.case_id
            for result
            in graded
        }

        scores = [
            float(
                result.score
            )
            for result
            in graded
            if (
                result.score
                is not None
            )
        ]

        successes = sum(
            1
            for result
            in graded
            if (
                result.success
                is True
            )
        )

        verification_results = [
            item.verification
            for item
            in completed
            if (
                item.verification
                is not None
                and item
                .verification
                .available
            )
        ]

        verification_passes = sum(
            1
            for verification
            in verification_results
            if (
                verification.passed
                and verification
                .required_failures
                == 0
            )
        )

        verification_failures = sum(
            1
            for verification
            in verification_results
            if (
                verification
                .required_failures
                > 0
            )
        )

        return AggregateMetrics(
            total_cases=(
                len(
                    case_ids
                )
            ),

            total_runs=(
                len(
                    results
                )
            ),

            graded_cases=(
                len(
                    graded_case_ids
                )
            ),

            graded_runs=(
                len(
                    graded
                )
            ),

            skipped_runs=sum(
                1
                for result
                in results
                if result.skipped
            ),

            successes=(
                successes
            ),

            success_rate=(
                (
                    successes
                    /
                    len(
                        graded
                    )
                )
                if graded
                else 0.0
            ),

            average_score=(
                mean(
                    scores
                )
                if scores
                else 0.0
            ),

            average_duration_seconds=(
                mean(
                    result
                    .duration_seconds
                    for result
                    in completed
                )
                if completed
                else 0.0
            ),

            average_tool_calls=(
                mean(
                    result
                    .tool_calls
                    for result
                    in completed
                )
                if completed
                else 0.0
            ),

            average_total_tokens=(
                mean(
                    result
                    .total_tokens
                    for result
                    in completed
                )
                if completed
                else 0.0
            ),

            average_retries=(
                mean(
                    result
                    .retry_count
                    for result
                    in completed
                )
                if completed
                else 0.0
            ),

            tool_errors=sum(
                result.tool_errors
                for result
                in completed
            ),

            safety_violations=sum(
                result
                .safety_violations
                for result
                in completed
            ),

            interrupted_runs=sum(
                1
                for result
                in completed
                if result.interrupted
            ),

            verification_available_runs=(
                len(
                    verification_results
                )
            ),

            verification_decisive_runs=sum(
                1
                for verification
                in verification_results
                if verification.decisive
            ),

            verification_failed_runs=(
                verification_failures
            ),

            verification_pass_rate=(
                (
                    verification_passes
                    /
                    len(
                        verification_results
                    )
                )

                if verification_results

                else 0.0
            ),
        )

    # ------------------------------------------------------------------
    # Comparison formula
    # ------------------------------------------------------------------

    @classmethod
    def _compare(
        cls,
        skill: Skill,
        baseline: AggregateMetrics,
        candidate: AggregateMetrics,
    ) -> SkillComparison:
        cfg = (
            skill
            .eval_config
        )

        success_delta = (
            candidate
            .success_rate
            -
            baseline
            .success_rate
        )

        score_delta = (
            candidate
            .average_score
            -
            baseline
            .average_score
        )

        token_gain = (
            cls
            ._relative_improvement(
                baseline
                .average_total_tokens,

                candidate
                .average_total_tokens,
            )
        )

        tool_gain = (
            cls
            ._relative_improvement(
                baseline
                .average_tool_calls,

                candidate
                .average_tool_calls,
            )
        )

        duration_gain = (
            cls
            ._relative_improvement(
                baseline
                .average_duration_seconds,

                candidate
                .average_duration_seconds,
            )
        )

        retry_gain = (
            cls
            ._relative_improvement(
                baseline
                .average_retries,

                candidate
                .average_retries,
            )
        )

        # Formula discussed earlier:
        #
        # 0.35 tokens
        # 0.30 tool calls
        # 0.25 duration
        # 0.10 retries
        #
        # These are initial engineering defaults, not scientifically
        # established constants.

        weight_sum = (
            cfg
            .token_efficiency_weight
            +
            cfg
            .tool_efficiency_weight
            +
            cfg
            .duration_efficiency_weight
            +
            cfg
            .retry_efficiency_weight
        )

        if weight_sum <= 0:
            efficiency_gain = (
                0.0
            )

        else:
            efficiency_gain = (
                (
                    cfg
                    .token_efficiency_weight
                    * token_gain
                )
                +
                (
                    cfg
                    .tool_efficiency_weight
                    * tool_gain
                )
                +
                (
                    cfg
                    .duration_efficiency_weight
                    * duration_gain
                )
                +
                (
                    cfg
                    .retry_efficiency_weight
                    * retry_gain
                )
            ) / weight_sum

        reasons: list[str] = []

        # -----------------------------------
        # 1. Safety gate
        # -----------------------------------

        safety_pass = (
            candidate
            .safety_violations
            == 0
        )

        if not safety_pass:
            reasons.append(
                "candidate produced "
                "a safety violation"
            )

        # -----------------------------------
        # 1b. Deterministic state pass gate
        # -----------------------------------

        state_verification_pass = (
            candidate
            .verification_failed_runs
            == 0
        )

        if not state_verification_pass:
            reasons.append(
                "candidate failed "
                "deterministic outcome "
                "verification"
            )

        # -----------------------------------
        # 2. Reliability gate
        # -----------------------------------

        success_regression = (
            success_delta
            <
            -cfg
            .maximum_success_rate_regression
        )

        score_regression = (
            score_delta
            <
            -cfg
            .maximum_score_regression
        )

        reliability_pass = (
            not success_regression
            and not score_regression
            and candidate
            .success_rate
            >= cfg
            .minimum_candidate_success_rate
        )

        if success_regression:
            reasons.append(
                "candidate success rate "
                "regressed beyond "
                "the allowed threshold"
            )

        if score_regression:
            reasons.append(
                "candidate quality score "
                "regressed beyond "
                "the allowed threshold"
            )

        if (
            candidate
            .success_rate
            <
            cfg
            .minimum_candidate_success_rate
        ):
            reasons.append(
                "candidate success rate "
                "is below minimum"
            )

        # -----------------------------------
        # 3. Generalization gate
        # -----------------------------------

        generalization_pass = (
            candidate
            .graded_cases
            >= cfg
            .minimum_graded_cases
            and candidate
            .interrupted_runs
            == 0
        )

        if (
            candidate
            .graded_cases
            <
            cfg
            .minimum_graded_cases
        ):
            reasons.append(
                "not enough distinct "
                "held-out cases were graded"
            )

        if (
            candidate
            .interrupted_runs
            > 0
        ):
            reasons.append(
                "automatic evaluation "
                "required user approval"
            )

        # -----------------------------------
        # Hard resource-regression caps
        # -----------------------------------

        resource_caps_pass = True

        if (
            token_gain
            <
            -cfg
            .maximum_token_regression
        ):
            resource_caps_pass = False

            reasons.append(
                "token use regressed "
                "beyond allowed threshold"
            )

        if (
            duration_gain
            <
            -cfg
            .maximum_duration_regression
        ):
            resource_caps_pass = False

            reasons.append(
                "runtime regressed "
                "beyond allowed threshold"
            )

        if (
            tool_gain
            <
            -cfg
            .maximum_tool_call_regression
        ):
            resource_caps_pass = False

            reasons.append(
                "tool-call count regressed "
                "beyond allowed threshold"
            )

        # -----------------------------------
        # 4. Improvement gate
        # -----------------------------------

        success_win = (
            success_delta
            >= cfg
            .minimum_success_rate_gain
        )

        score_win = (
            score_delta
            >= cfg
            .minimum_score_gain
        )

        efficiency_win = (
            efficiency_gain
            >= cfg
            .minimum_efficiency_gain
        )

        # Efficiency is only allowed to justify promotion when quality
        # did not regress.
        efficiency_only_win = (
            efficiency_win
            and success_delta
            >= -cfg
            .maximum_success_rate_regression
            and score_delta
            >= -cfg
            .maximum_score_regression
        )

        improvement_pass = (
            resource_caps_pass
            and (
                success_win
                or score_win
                or efficiency_only_win
            )
        )

        if success_win:
            reasons.append(
                "candidate improved "
                "task success rate"
            )

        if score_win:
            reasons.append(
                "candidate improved "
                "task-quality score"
            )

        if efficiency_only_win:
            reasons.append(
                "candidate preserved quality "
                "while improving efficiency"
            )

        return SkillComparison(
            success_rate_delta=(
                success_delta
            ),

            score_delta=(
                score_delta
            ),

            token_improvement=(
                token_gain
            ),

            tool_call_improvement=(
                tool_gain
            ),

            duration_improvement=(
                duration_gain
            ),

            retry_improvement=(
                retry_gain
            ),

            efficiency_gain=(
                efficiency_gain
            ),

            safety_pass=(
                safety_pass
            ),

            state_verification_pass=(
                state_verification_pass
            ),

            reliability_pass=(
                reliability_pass
            ),

            generalization_pass=(
                generalization_pass
            ),

            improvement_pass=(
                improvement_pass
            ),

            reasons=(
                reasons
            ),
        )

    # ------------------------------------------------------------------
    # Final verdict
    # ------------------------------------------------------------------

    @staticmethod
    def _verdict(
        comparison: SkillComparison,
        baseline: AggregateMetrics,
        candidate: AggregateMetrics,
    ) -> str:
        """
        No global weighted score can override a failed safety/reliability gate.
        """

        if (
            candidate
            .safety_violations
            > 0
        ):
            return "fail"

        # No automatic verdict if we could not obtain enough evidence.
        if (
            candidate
            .graded_cases
            == 0
        ):
            return (
                "needs_review"
            )

        if (
            candidate
            .interrupted_runs
            > 0
        ):
            return (
                "needs_review"
            )

        if (
            candidate
            .skipped_runs
            ==
            candidate
            .total_runs
        ):
            return (
                "needs_review"
            )

        if not (
            comparison
            .safety_pass
        ):
            return "fail"

        if not (
            comparison
            .state_verification_pass
        ):
            return "fail"

        if not (
            comparison
            .reliability_pass
        ):
            return "fail"

        if not (
            comparison
            .generalization_pass
        ):
            return (
                "needs_review"
            )

        if not (
            comparison
            .improvement_pass
        ):
            return "fail"

        return "pass"

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    @staticmethod
    def _relative_improvement(
        baseline: float,
        candidate: float,
    ) -> float:
        """
        Positive = candidate used less.

        Example:

            baseline = 100
            candidate = 80

            gain = +0.20
        """

        if baseline <= 0:
            if candidate <= 0:
                return 0.0

            # Baseline used zero but candidate now uses resource.
            return -1.0

        return (
            baseline
            - candidate
        ) / baseline