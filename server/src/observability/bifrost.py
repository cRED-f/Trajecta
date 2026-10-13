"""Inject W3C tracing context only into Trajecta -> Bifrost HTTP requests.

The OpenAI-compatible SDK can retry its own HTTP calls; injection happens at
request-send time so each attempt uses the current active model span. Do not
instrument arbitrary HTTPX clients (which could export private URLs/headers).
"""
from __future__ import annotations

import os
from typing import Any

from opentelemetry import trace
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator


def _enabled() -> bool:
    return os.getenv("TRAJECTA_PHOENIX_ENABLED", "false").lower() in {"1", "true", "yes", "on"}


def inject_bifrost_context(request: Any) -> None:
    """Do not change the request when tracing is disabled or no span exists."""
    if not _enabled() or not trace.get_current_span().get_span_context().is_valid:
        return
    carrier: dict[str, str] = {}
    TraceContextTextMapPropagator().inject(carrier)
    for name, value in carrier.items():
        # Never overwrite headers explicitly set upstream.
        if name not in request.headers:
            request.headers[name] = value


async def inject_bifrost_context_async(request: Any) -> None:
    inject_bifrost_context(request)
