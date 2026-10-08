"""Opt-in safe live activity callbacks for candidate evaluations."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

EvaluationEvent = Callable[[dict[str, Any]], Awaitable[None]]


async def emit(
    callback: EvaluationEvent | None,
    kind: str,
    **fields: Any,
) -> None:
    if callback is not None:
        await callback({"type": kind, **fields})
