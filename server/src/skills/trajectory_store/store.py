"""Trajectory Store — raw execution data, separate from agent memory.

Stores per-task raw execution history used by the skill miner, evaluation,
observability, debugging, and regression analysis.
"""

from __future__ import annotations


class TrajectoryStore:
    """Persists raw trajectories: tasks, agent states, plans, model calls, tool calls, outcomes."""

    # TODO: capture + persist trajectory events, replay query interface
    pass