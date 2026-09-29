"""Regression detection between two evaluation metric snapshots.

Given a "before" and an "after" metrics snapshot (typically
``AggregateMetrics`` dumps from two evaluations), report whether the newer
snapshot is worse than the older one. Rollback decisions can consult this
before restoring a previous skill version.
"""

from __future__ import annotations

from typing import Any

# AggregateMetrics stores this as `tool_errors`; accept `tool_failures` too so
# callers can use either spelling.
_TOOL_FAILURE_KEYS = ("tool_failures", "tool_errors")


def _metrics(source: Any) -> dict[str, Any]:
    """Normalize an AggregateMetrics model (or plain dict) into a dict."""

    if hasattr(source, "model_dump"):
        return source.model_dump()
    if isinstance(source, dict):
        return source
    raise TypeError(f"unsupported metrics type: {type(source).__name__}")


def _get(metrics: dict[str, Any], *keys: str) -> float:
    for key in keys:
        if metrics.get(key) is not None:
            return float(metrics[key])
    return 0.0


class SkillRegressionDetector:
    """Flag metric regressions between two snapshots of the same skill."""

    def detect(self, before: dict, after: dict) -> dict:
        before_metrics = _metrics(before)
        after_metrics = _metrics(after)

        regressions: list[str] = []

        if _get(after_metrics, "success_rate") < _get(before_metrics, "success_rate"):
            regressions.append("success_rate_drop")

        if _get(after_metrics, *_TOOL_FAILURE_KEYS) > _get(
            before_metrics, *_TOOL_FAILURE_KEYS
        ):
            regressions.append("tool_failure_increase")

        return {
            "regression": bool(regressions),
            "issues": regressions,
        }
