"""Token usage tracking and reporting per task, per session, per provider."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from server.src.llm_gateway.clients import Usage


@dataclass(slots=True)
class ProviderUsage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def add(self, usage: Usage) -> None:
        self.calls += 1
        self.input_tokens += usage.input_tokens
        self.output_tokens += usage.output_tokens
        if usage.cost_usd is not None:
            self.cost_usd += usage.cost_usd

    def to_json(self) -> dict[str, Any]:
        return {
            "calls": self.calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "cost_usd": self.cost_usd,
        }


class UsageTracker:
    """Aggregates usage across providers, scoped to a task or session."""

    def __init__(self) -> None:
        self._by_provider: dict[str, ProviderUsage] = defaultdict(ProviderUsage)
        self._by_task: dict[str, dict[str, ProviderUsage]] = defaultdict(lambda: defaultdict(ProviderUsage))

    def record(self, provider: str, usage: Usage, *, task_id: str | None = None) -> None:
        self._by_provider[provider].add(usage)
        if task_id is not None:
            self._by_task[task_id][provider].add(usage)

    def get_provider_usage(self, provider: str) -> ProviderUsage:
        return self._by_provider[provider]

    def get_task_usage(self, task_id: str) -> dict[str, ProviderUsage]:
        return dict(self._by_task[task_id])

    def get_all_usage(self) -> dict[str, ProviderUsage]:
        return dict(self._by_provider)

    def reset(self) -> None:
        self._by_provider.clear()
        self._by_task.clear()