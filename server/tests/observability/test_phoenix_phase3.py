"""Phase 3 local read-only diagnostics; no external Phoenix server required."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from server.src.observability.reporting import summarize_trajectories
import importlib.util
from pathlib import Path

_path = Path(__file__).resolve().parents[2] / "src/api/routes/traces.py"
_spec = importlib.util.spec_from_file_location("phase3_route_module", _path)
assert _spec is not None and _spec.loader is not None
routes = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(routes)
list_traces = routes.list_traces
observability_status = routes.observability_status


def test_no_private_trajectory_data_leaks():
    records = [{
        "id": "tr-1", "created_at": "2026-10-14T00:00:00Z", "outcome": "completed",
        "steps": '[{"input":"DO-NOT-EXPORT"}]',
        "metadata": json.dumps({
            "run_id": "r-1", "trace_id": "a" * 32,
            "private_goal": "DO-NOT-EXPORT", "run_metrics": {
                "duration_seconds": 10, "ttft_seconds": 1,
                "input_tokens": 100, "output_tokens": 40, "tool_calls": 2, "tool_errors": 1,
            },
        }),
    }]
    report = summarize_trajectories(records)
    assert "DO-NOT-EXPORT" not in json.dumps(report)
    assert report["usage"]["estimated_cost_usd"] is None
    assert report["recent"][0]["trace_id"] == "a" * 32
    assert report["outcomes"]["completed"] == 1
    assert report["latency_seconds"]["p95"] == 10


def test_invalid_data_excluded_and_metrics_not_invented():
    records = [{"id": "abc", "created_at": "2026-10-14T00:00:00Z", "outcome": None,
                "metadata": '{"trace_id": "bad", "run_metrics": {"duration_seconds": -1, "input_tokens": "100"}}'}]
    result = summarize_trajectories(records)
    assert result["recent"][0]["trace_id"] is None
    assert result["latency_seconds"]["count"] == 0
    assert result["latency_seconds"]["p99"] is None
    assert result["usage"]["input_tokens"] == 0
    assert result["outcomes"]["running"] == 1


def test_percentiles_on_bounded_sample():
    rows = [
        {"id": str(i), "created_at": "now", "outcome": "completed",
         "metadata": json.dumps({"run_metrics": {"duration_seconds": float(i)}})}
        for i in (1, 2, 3, 4, 5)
    ]
    result = summarize_trajectories(rows)
    assert result["latency_seconds"]["average"] == 3
    assert result["latency_seconds"]["p50"] == 3
    assert result["latency_seconds"]["p95"] == 4.8


@pytest.mark.asyncio
async def test_summary_route_reads_only_safe_columns():
    seen = []
    class StubDb:
        async def fetch(self, statement):
            seen.append(statement)
            return [{"id": "1", "created_at": "now", "outcome": "failure", "metadata": "{}"}]
    req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        memory_provider=SimpleNamespace(sqlite=StubDb()))))
    report = await list_traces(req)
    assert "steps" not in seen[0] and "goal" not in seen[0]
    assert report["recent"][0]["outcome"] == "failure"


@pytest.mark.asyncio
async def test_status_endpoint_is_safe_offline(monkeypatch):
    monkeypatch.setattr(routes, "_phoenix_reachable", lambda: False)
    monkeypatch.setenv("TRAJECTA_PHOENIX_ENABLED", "false")
    monkeypatch.setenv("TRAJECTA_PHOENIX_ENDPOINT", "http://evil.example/v1/traces")
    result = await observability_status()
    assert result["connected"] is False
    assert result["dashboard_url"] is None
    assert result["collector_endpoint"].startswith("http://127.0.0.1:")
    assert result["capture_content"] is False
