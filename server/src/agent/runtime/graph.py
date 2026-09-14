"""LangGraph execution graph definition for the Trajecta agent.

Defines the stateful execution graph: plan → route → execute tools → verify → store trajectory.
"""

from __future__ import annotations


# TODO: define LangGraph StateGraph with these nodes:
#   - receive_task
#   - retrieve_memory_and_skills
#   - plan
#   - execute (direct or via subagents)
#   - guardrail_check
#   - run_tool
#   - verify
#   - store_trajectory
#   - skill_mine (async, post-completion)


class TrajectaGraph:
    """Placeholder for the main LangGraph agent graph."""

    # TODO: implement build() -> CompiledGraph
    pass
