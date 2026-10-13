"""Bounded, content-free operation spans on non-LangChain service boundaries.

Do not record argument values, output text, exception details, SQL, paths or
private user-supplied names. LangChain spans already cover standard tools.
"""
from __future__ import annotations

import asyncio
import functools
from collections.abc import Callable
from typing import Any

from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

tracer = trace.get_tracer("trajecta.operations")


def observed(name: str, *, result_count: bool = False) -> Callable[..., Any]:
    """Decorate an async service method. Reraise every exception unchanged."""
    def decorate(fn: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            with tracer.start_as_current_span(
                name, record_exception=False, set_status_on_exception=False
            ) as span:
                try:
                    result = await fn(*args, **kwargs)
                    if result_count and isinstance(result, (list, tuple)):
                        span.set_attribute("trajecta.result_count", len(result))
                    if name.startswith("trajecta.guardrails.") and hasattr(result, "findings"):
                        findings = result.findings
                        span.set_attribute("trajecta.guardrails.findings", len(findings))
                        if findings:
                            span.set_attribute("trajecta.guardrails.action", findings[0].action)
                    return result
                except asyncio.CancelledError:
                    span.set_attribute("trajecta.outcome", "cancelled")
                    raise
                except BaseException as exc:
                    span.set_attribute("trajecta.error_type", type(exc).__name__)
                    span.set_status(Status(StatusCode.ERROR))
                    raise
        return wrapper
    return decorate


def safe_status(status: Any) -> str:
    """Permit only a fixed status vocabulary (no untrusted exception data)."""
    return str(status) if status in {
        "completed", "failed", "pending", "skipped", "processing",
        "rejected", "promoted", "active", "blocked", "redacted",
    } else "other"
