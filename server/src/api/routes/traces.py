"""Read-only local observability summaries and Phoenix connection diagnostics.

Do not expose raw trajectories: they may contain private prompts and tool data.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from urllib.error import URLError
from urllib.request import Request as URLRequest, urlopen

from fastapi import APIRouter, Request

from server.src.observability.reporting import summarize_trajectories

router = APIRouter(prefix="/traces", tags=["traces"])


def _local_endpoint() -> str:
    # Do not turn this into a browser-controllable proxy or permit SSRF.
    configured = os.getenv("TRAJECTA_PHOENIX_ENDPOINT", "http://127.0.0.1:6006/v1/traces")
    if configured in {"http://localhost:6006/v1/traces", "http://127.0.0.1:6006/v1/traces"}:
        return configured.replace("localhost", "127.0.0.1")
    return "http://127.0.0.1:6006/v1/traces"


def _phoenix_reachable() -> bool:
    try:
        with urlopen(URLRequest("http://127.0.0.1:6006/", method="GET"), timeout=0.7) as response:
            return response.status == 200
    except (OSError, URLError, TimeoutError):
        return False


def _phoenix_project_id(project_name: str) -> str | None:
    """Resolve a Phoenix UI project ID if the local server supports its REST API."""
    try:
        with urlopen(URLRequest("http://127.0.0.1:6006/v1/projects?limit=100", method="GET"), timeout=0.7) as response:
            document = json.load(response)
        items = document if isinstance(document, list) else document.get("data", document.get("projects", []))
        if not isinstance(items, list):
            return None
        for item in items:
            if isinstance(item, dict) and item.get("name") == project_name:
                identifier = item.get("id")
                if isinstance(identifier, str) and re.fullmatch(r"[A-Za-z0-9_-]{6,200}={0,2}", identifier):
                    return identifier
    except (OSError, ValueError, TypeError, KeyError):
        return None
    return None


@router.get("/status")
async def observability_status() -> dict:
    connected = await asyncio.to_thread(_phoenix_reachable)
    project = os.getenv("TRAJECTA_PHOENIX_PROJECT", "trajecta")
    project_id = await asyncio.to_thread(_phoenix_project_id, project) if connected else None
    enabled = os.getenv("TRAJECTA_PHOENIX_ENABLED", "false").lower() in {"true", "yes", "1", "on"}
    return {
        "enabled": enabled,
        "connected": connected,
        "dashboard_url": "http://127.0.0.1:6006" if connected else None,
        "collector_endpoint": _local_endpoint(),
        "project": project,
        "project_id": project_id,
        "capture_content": False,
    }


@router.get("")
async def list_traces(request: Request) -> dict:
    # The underlying store contains user content. Query only the safe columns.
    db = request.app.state.memory_provider.sqlite
    rows = await db.fetch(
        "SELECT id, created_at, outcome, metadata FROM trajectories "
        "ORDER BY created_at DESC LIMIT 500"
    )
    result = summarize_trajectories(rows)
    result["source"] = "trajecta_trajectories"
    result["note"] = "Recent 500 recorded runs; completion is not verified task success. Cost is unavailable."
    return result
