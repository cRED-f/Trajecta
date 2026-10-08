"""Attribute live agent runs to the skills they actually used.

A skill is never injected into a run passively (``agent/context/manager.py``
is an unused stub), so the only execution signal is the agent reading the
skill with the ``skill_view`` tool. One metrics row is recorded per
(trajectory, distinct skill) once the trajectory reaches a scored outcome.
"""

from __future__ import annotations

import json
import logging

from datetime import UTC, datetime
from typing import Any

from server.src.skills.analytics.metrics import SkillMetricsCollector
from server.src.skills.repository import SkillRepository
from server.src.skills.trajectory_store import TrajectoryStore

logger = logging.getLogger(__name__)

# Only a completed run says anything about skill quality: an approval pause
# or a user cancel is not a scored execution, and an in-flight run has no
# outcome yet.
RECORDED_OUTCOMES = frozenset({"success", "failure", "completed"})

# The same failure statuses the replay evaluator counts as tool errors
# (skills/evaluation/replay.py), so both sides agree on what a failure is.
FAILED_STATUSES = frozenset({"error", "failed", "failure"})

SKILL_VIEW_TOOL = "skill_view"

# A skill with no registered version still gets a row in its own bucket:
# totals stay right without polluting version-scoped comparisons.
UNKNOWN_VERSION = "unknown"


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None

    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None

    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _args_text(value: Any) -> str:
    """One streamed args fragment as text (chunks may be None or a dict)."""

    if isinstance(value, str):
        return value

    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)

    return ""


def _skill_name_from_args(args: str) -> str | None:
    """Recover the ``name=`` argument passed to skill_view."""

    if not args.strip():
        return None

    try:
        payload = json.loads(args)
    except json.JSONDecodeError:
        return None

    if not isinstance(payload, dict):
        return None

    name = payload.get("name")

    if not isinstance(name, str) or not name.strip():
        return None

    return name.strip()


class SkillExecutionAttributor:
    """Write one execution-metrics row per skill used during a run."""

    def __init__(
        self,
        *,
        trajectories: TrajectoryStore,
        repository: SkillRepository,
        metrics: SkillMetricsCollector,
    ) -> None:
        self._trajectories = trajectories
        self._repository = repository
        self._metrics = metrics

    async def attribute(self, trajectory_id: str) -> list[str]:
        """Record every skill this trajectory used. Returns the names stored.

        Reading a trajectory that does not exist, or one that has not reached
        a scored outcome, records nothing and returns an empty list. Real
        failures (a broken database) still propagate to the caller, which is
        expected to log them without breaking the user's task.
        """

        trajectory = await self._trajectories.get(trajectory_id)

        if trajectory is None:
            return []

        outcome = trajectory.get("outcome")

        if outcome not in RECORDED_OUTCOMES:
            return []

        steps = trajectory.get("steps")

        if not isinstance(steps, list) or not steps:
            return []

        views = self._skill_views(steps)

        if not views:
            return []

        # A completed response is an observation, not a quality verdict.
        success = None if outcome == "completed" else outcome == "success"
        latency_ms = self._latency_ms(trajectory, steps)
        tokens = self._total_tokens(steps)
        failures = self._tool_failures(steps)
        experiments = self._experiment_context(steps)

        recorded: list[str] = []

        # First successful view per skill, in the order the run loaded them.
        for skill_name, view_index in views.items():
            failures_after_load = sum(
                1 for index in failures if index > view_index
            )
            arm = experiments.get(skill_name) or {}

            await self._metrics.record(
                skill_name=skill_name,
                skill_version=await self._version(skill_name),
                trajectory_id=trajectory_id,
                success=success,
                latency_ms=latency_ms,
                tokens=tokens,
                tool_failures=failures_after_load,
                experiment_id=arm.get("experiment_id"),
                arm_kind=str(arm.get("arm_kind") or "active"),
                unit_id=arm.get("unit_id"),
            )

            recorded.append(skill_name)

        return recorded

    @staticmethod
    def _experiment_context(steps: list[Any]) -> dict[str, dict[str, Any]]:
        """Which experiment arms applied to this run, written at prepare().

        Without this an arm's samples land in the untagged bucket and the
        experiment never accumulates enough evidence to decide anything.
        """

        context: dict[str, dict[str, Any]] = {}

        for step in steps:
            if not isinstance(step, dict):
                continue

            if step.get("type") != "skill.assignments":
                continue

            data = step.get("data")

            if not isinstance(data, dict):
                continue

            assignments = data.get("assignments")

            if not isinstance(assignments, list):
                continue

            for item in assignments:
                if not isinstance(item, dict):
                    continue

                skill_name = item.get("skill_name")

                if not isinstance(skill_name, str) or not skill_name:
                    continue

                context[skill_name] = {
                    "experiment_id": item.get("experiment_id"),
                    "arm_kind": item.get("arm_kind"),
                    "unit_id": item.get("unit_id"),
                }

        return context

    # ------------------------------------------------------------------
    # trajectory inspection
    # ------------------------------------------------------------------

    def _skill_views(self, steps: list[Any]) -> dict[str, int]:
        """Distinct skills read via skill_view, mapped to the step index of
        their first *successful* result.

        A failed view (unknown skill, tool error) never executed anything, so
        it is ignored rather than credited to a skill.
        """

        views: dict[str, int] = {}

        for call in self._completed_calls(steps):
            if call["tool"] != SKILL_VIEW_TOOL:
                continue

            if call["result_index"] is None:
                continue

            if call["status"] in FAILED_STATUSES:
                continue

            content = call.get("content")

            if content is None or content == "" or content == "None":
                continue

            skill_name = _skill_name_from_args(str(call["args"]))

            if skill_name is None:
                continue

            seen = views.get(skill_name)

            if seen is None or call["result_index"] < seen:
                views[skill_name] = int(call["result_index"])

        return views

    def _completed_calls(self, steps: list[Any]) -> list[dict[str, Any]]:
        """Reassemble streamed tool calls and pair each with its result.

        ``tool.call.delta`` events carry string *fragments*: only the first
        chunk has an id and a tool name, and later chunks stream the args. The
        desktop client reconstructs the same stream in ``updateTool()``
        (chat-store.ts), keyed the same way — id first, then tool name, then
        the most recent still-running call.

        Two deliberate differences from that UI algorithm: a chunk carrying an
        id never seen before opens a new call rather than falling through to
        the last running one (parallel tool calls arrive in one token, and
        merging them would lose the second skill's name), and a result matches
        by id or by tool name only — attaching it to an unrelated call would
        credit a skill that never ran. Both resolve to "skip" rather than
        "guess" when recovery fails.
        """

        calls: list[dict[str, Any]] = []

        for index, step in enumerate(steps):
            if not isinstance(step, dict):
                continue

            data = step.get("data") if isinstance(step.get("data"), dict) else {}
            event_type = step.get("type")

            if event_type == "tool.call.delta":
                self._merge_call_delta(calls, data)
            elif event_type == "tool.result":
                self._close_call(calls, data, index)

        return calls

    @staticmethod
    def _open_call(
        calls: list[dict[str, Any]],
        call_id: str | None,
        name: str | None,
        fragment: str,
    ) -> None:
        calls.append(
            {
                "id": call_id,
                "tool": name,
                "args": fragment,
                "result_index": None,
                "status": None,
                "content": None,
            }
        )

    @classmethod
    def _merge_call_delta(cls, calls: list[dict[str, Any]], data: dict[str, Any]) -> None:
        call_id = data.get("id") if isinstance(data.get("id"), str) else None
        name = data.get("name") if isinstance(data.get("name"), str) else None
        fragment = _args_text(data.get("args"))

        target = -1

        if call_id is not None:
            target = next(
                (i for i, call in enumerate(calls) if call["id"] == call_id),
                -1,
            )

            if target == -1:
                # An id we have never seen opens a new call. The fallbacks
                # below exist for id-less fragments; letting a known-new id
                # reach them would merge two parallel tool calls into one and
                # lose the second skill's name.
                cls._open_call(calls, call_id, name, fragment)
                return

        if target == -1 and name is not None:
            target = next(
                (
                    i
                    for i, call in enumerate(calls)
                    if call["id"] is None
                    and call["tool"] == name
                    and call["result_index"] is None
                ),
                -1,
            )

        if target == -1:
            target = next(
                (
                    i
                    for i in range(len(calls) - 1, -1, -1)
                    if calls[i]["result_index"] is None
                ),
                -1,
            )

        if target == -1:
            cls._open_call(calls, call_id, name, fragment)
            return

        call = calls[target]
        call["id"] = call_id or call["id"]
        call["tool"] = name or call["tool"]
        call["args"] = str(call["args"]) + fragment

    @staticmethod
    def _close_call(
        calls: list[dict[str, Any]],
        data: dict[str, Any],
        index: int,
    ) -> None:
        result_id = (
            data.get("tool_call_id") if isinstance(data.get("tool_call_id"), str) else None
        )
        tool_name = data.get("name") if isinstance(data.get("name"), str) else None

        target = -1

        if result_id is not None:
            target = next(
                (i for i, call in enumerate(calls) if call["id"] == result_id),
                -1,
            )

        if target == -1 and tool_name is not None:
            target = next(
                (
                    i
                    for i, call in enumerate(calls)
                    if call["tool"] == tool_name and call["result_index"] is None
                ),
                -1,
            )

        if target == -1:
            return

        call = calls[target]
        call["result_index"] = index
        call["status"] = data.get("status")
        call["content"] = data.get("content")

    def _tool_failures(self, steps: list[Any]) -> list[int]:
        """Step indexes of failed tool results, for post-load attribution."""

        failures: list[int] = []

        for index, step in enumerate(steps):
            if not isinstance(step, dict) or step.get("type") != "tool.result":
                continue

            data = step.get("data") if isinstance(step.get("data"), dict) else {}

            if data.get("status") in FAILED_STATUSES:
                failures.append(index)

        return failures

    @staticmethod
    def _latency_ms(trajectory: dict[str, Any], steps: list[Any]) -> float:
        """Elapsed time from the run's start to its last recorded event."""

        started = _parse_time(trajectory.get("created_at"))

        if started is None:
            return 0.0

        ended: datetime | None = None

        for step in reversed(steps):
            if isinstance(step, dict):
                ended = _parse_time(step.get("at"))

                if ended is not None:
                    break

        if ended is None:
            ended = datetime.now(UTC)

        elapsed = (ended - started).total_seconds() * 1000

        return round(elapsed, 3) if elapsed > 0 else 0.0

    @staticmethod
    def _total_tokens(steps: list[Any]) -> int:
        """Token total reported by run.finished.

        The runtime emits one cumulative figure per run; taking the max keeps
        this right if a nested run ever reports as well.
        """

        totals: list[int] = []

        for step in steps:
            if not isinstance(step, dict) or step.get("type") != "run.finished":
                continue

            data = step.get("data") if isinstance(step.get("data"), dict) else {}
            tokens = data.get("tokens")

            if not isinstance(tokens, dict):
                continue

            total = tokens.get("total")

            if isinstance(total, int) and total > 0:
                totals.append(total)

        return max(totals, default=0)

    async def _version(self, skill_name: str) -> str:
        record = await self._repository.get_active(skill_name)

        if record is None:
            return UNKNOWN_VERSION

        return str(record.get("version") or UNKNOWN_VERSION)
