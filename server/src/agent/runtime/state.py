"""Agent state schema for the LangGraph execution graph."""

from __future__ import annotations

from typing import Any
from pydantic import BaseModel


class AgentState(BaseModel):
    """Shared state flowing through the execution graph."""

    task_id: str
    goal: str
    plan: list[str] = []
    current_step: int = 0
    tool_calls: list[dict[str, Any]] = []
    tool_results: list[dict[str, Any]] = []
    errors: list[str] = []
    subagents: list[str] = []
    final_result: str | None = None
    status: str = "pending"  # pending | running | complete | failed
