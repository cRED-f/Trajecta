"""Deterministic verification over tool execution events."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from server.src.skills.representation.skill import (
    SkillEvalCase,
    ToolAssertion,
    ToolAssertionType,
)


class ToolExecutionEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    tool_call_id: str | None = None
    status: str = "success"
    arguments: dict[str, Any] = Field(default_factory=dict)
    result_excerpt: str = ""


class ToolAssertionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str
    tool_name: str
    required: bool
    weight: float
    passed: bool
    score: float = Field(ge=0.0, le=1.0)
    reason: str
    actual: Any = None
    expected: Any = None


class ToolVerificationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    available: bool = False
    passed: bool = True
    score: float = Field(default=1.0, ge=0.0, le=1.0)
    required_failures: int = 0
    assertions: list[ToolAssertionResult] = Field(default_factory=list)


class ToolEffectVerifier:
    def verify(
        self,
        *,
        case: SkillEvalCase,
        events: list[ToolExecutionEvent],
    ) -> ToolVerificationResult:
        if not case.tool_assertions:
            return ToolVerificationResult(available=False)

        results = [
            self._verify_one(assertion, events)
            for assertion in case.tool_assertions
        ]

        required_failures = sum(
            1
            for item in results
            if item.required and not item.passed
        )

        total_weight = sum(item.weight for item in results)

        score = (
            (
                sum(item.weight * item.score for item in results) / total_weight
            )
            if total_weight > 0
            else 1.0
        )

        return ToolVerificationResult(
            available=True,
            passed=required_failures == 0,
            score=max(0.0, min(1.0, score)),
            required_failures=required_failures,
            assertions=results,
        )

    @staticmethod
    def _matching(
        assertion: ToolAssertion,
        events: list[ToolExecutionEvent],
    ) -> list[ToolExecutionEvent]:
        return [
            event
            for event in events
            if event.name == assertion.tool_name
        ]

    def _verify_one(
        self,
        assertion: ToolAssertion,
        events: list[ToolExecutionEvent],
    ) -> ToolAssertionResult:
        matching = self._matching(assertion, events)
        kind = assertion.type

        if kind == ToolAssertionType.TOOL_SUCCEEDED:
            successful = [
                event
                for event in matching
                if event.status.lower() not in {"error", "failed", "failure"}
            ]

            if assertion.result_contains:
                needle = assertion.result_contains.casefold()
                successful = [
                    event
                    for event in successful
                    if needle in event.result_excerpt.casefold()
                ]

            passed = bool(successful)
            return self._result(
                assertion,
                passed,
                f"{len(successful)} successful matching call(s)",
                actual=len(successful),
                expected=">= 1",
            )

        if kind == ToolAssertionType.TOOL_NOT_USED:
            passed = not matching
            return self._result(
                assertion,
                passed,
                f"{len(matching)} matching call(s)",
                actual=len(matching),
                expected=0,
            )

        if kind == ToolAssertionType.TOOL_CALLS_AT_LEAST:
            count = assertion.count if assertion.count is not None else 1
            passed = len(matching) >= count
            return self._result(
                assertion,
                passed,
                f"{len(matching)} call(s), minimum {count}",
                actual=len(matching),
                expected={"minimum": count},
            )

        if kind == ToolAssertionType.TOOL_CALLS_AT_MOST:
            count = assertion.count if assertion.count is not None else 0
            passed = len(matching) <= count
            return self._result(
                assertion,
                passed,
                f"{len(matching)} call(s), maximum {count}",
                actual=len(matching),
                expected={"maximum": count},
            )

        raise ValueError(f"unsupported tool assertion: {kind.value}")

    @staticmethod
    def _result(
        assertion: ToolAssertion,
        passed: bool,
        reason: str,
        *,
        actual: Any,
        expected: Any,
    ) -> ToolAssertionResult:
        return ToolAssertionResult(
            type=assertion.type.value,
            tool_name=assertion.tool_name,
            required=assertion.required,
            weight=assertion.weight,
            passed=passed,
            score=1.0 if passed else 0.0,
            reason=reason,
            actual=actual,
            expected=expected,
        )