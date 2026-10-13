"""Bounded, content-free diagnostics from existing trajectory records.

This is not a Phoenix mirror. No goals, prompts, tool payloads, or exception
messages are returned. Only recorded counters/durations are summarized.
"""
from __future__ import annotations

import json
import math
from typing import Any


def _nonnegative_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        return None
    number = float(value)
    return number if math.isfinite(number) and number >= 0 else None


def _percentile(values: list[float], percent: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percent
    low = math.floor(position)
    high = math.ceil(position)
    return round(ordered[low] + (ordered[high] - ordered[low]) * (position - low), 3)


def summarize_trajectories(rows: list[dict[str, Any]]) -> dict[str, Any]:
    durations: list[float] = []
    ttfts: list[float] = []
    input_tokens = output_tokens = tool_calls = tool_errors = 0
    outcomes: dict[str, int] = {}
    recent: list[dict[str, Any]] = []
    for row in rows:
        try:
            metadata = json.loads(row.get("metadata") or "{}")
        except (ValueError, TypeError):
            metadata = {}
        if not isinstance(metadata, dict):
            metadata = {}
        metrics = metadata.get("run_metrics") or {}
        if not isinstance(metrics, dict):
            metrics = {}
        duration = _nonnegative_number(metrics.get("duration_seconds"))
        ttft = _nonnegative_number(metrics.get("ttft_seconds"))
        if duration is not None:
            durations.append(duration)
        if ttft is not None:
            ttfts.append(ttft)
        for field in ("input_tokens", "output_tokens", "tool_calls", "tool_errors"):
            value = _nonnegative_number(metrics.get(field))
            if value is None:
                continue
            if field == "input_tokens":
                input_tokens += int(value)
            elif field == "output_tokens":
                output_tokens += int(value)
            elif field == "tool_calls":
                tool_calls += int(value)
            else:
                tool_errors += int(value)
        outcome = str(row.get("outcome") or "running")
        if outcome not in {"completed", "interrupted", "cancelled", "failure", "success", "running"}:
            outcome = "other"
        outcomes[outcome] = outcomes.get(outcome, 0) + 1
        if len(recent) < 30:
            run_id = metadata.get("run_id")
            trace_id = metadata.get("trace_id")
            recent.append({
                "trajectory_id": str(row["id"]),
                "run_id": run_id if isinstance(run_id, str) and len(run_id) <= 100 else None,
                "trace_id": trace_id if isinstance(trace_id, str) and len(trace_id) == 32
                and all(c in "0123456789abcdef" for c in trace_id.lower()) else None,
                "created_at": row.get("created_at"),
                "outcome": outcome,
                "duration_seconds": duration,
                "ttft_seconds": ttft,
            })
    return {
        "sample_size": len(rows),
        "outcomes": outcomes,
        "latency_seconds": {
            "count": len(durations),
            "average": round(sum(durations) / len(durations), 3) if durations else None,
            "p50": _percentile(durations, .5),
            "p95": _percentile(durations, .95),
            "p99": _percentile(durations, .99),
        },
        "ttft_seconds": {"count": len(ttfts), "p95": _percentile(ttfts, .95)},
        "usage": {
            "input_tokens": input_tokens, "output_tokens": output_tokens,
            "tool_calls": tool_calls, "tool_errors": tool_errors,
            "estimated_cost_usd": None,  # Not reliably recorded in trajectory metadata.
        },
        "recent": recent,
    }
