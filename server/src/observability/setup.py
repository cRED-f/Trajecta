"""Opt-in, single-provider Phoenix/OpenInference setup for the FastAPI process.

Phoenix is an independent local server. Failure to export telemetry must not
prevent Trajecta from starting or completing user work.
"""
from __future__ import annotations

import logging
import os
from threading import Lock
from typing import Any

from opentelemetry import trace
from opentelemetry.trace import ProxyTracerProvider
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased

logger = logging.getLogger(__name__)
_lock = Lock()
_provider: Any | None = None


def initialize_observability() -> bool:
    """Install tracing once, only when explicitly enabled.

    Returns False when disabled, dependencies missing, or another application
    already owns the global tracer provider. Does not probe the collector.
    """
    global _provider
    if os.getenv("TRAJECTA_PHOENIX_ENABLED", "false").strip().lower() not in {
        "1", "true", "yes", "on"
    }:
        return False
    with _lock:
        if _provider is not None:
            return True
        # register() owns the process-global provider. Never overwrite another
        # registered SDK (including those installed by external plugins).
        if not isinstance(trace.get_tracer_provider(), ProxyTracerProvider):
            logger.warning("Phoenix tracing skipped: global OpenTelemetry provider already exists")
            return False
        try:
            from phoenix.otel import register
            from openinference.instrumentation import TraceConfig
            from openinference.instrumentation.langchain import LangChainInstrumentor

            endpoint = os.getenv(
                "TRAJECTA_PHOENIX_ENDPOINT", "http://127.0.0.1:6006/v1/traces"
            ).strip()
            # Local-only by default. Remote collectors require deliberate
            # security review: this emits timing and model/tool metadata.
            if not endpoint.startswith(("http://127.0.0.1:", "http://localhost:")):
                if os.getenv("TRAJECTA_PHOENIX_ALLOW_REMOTE") != "true":
                    logger.warning("Phoenix endpoint rejected: remote export is disabled")
                    return False

            try:
                sampling = float(os.getenv("TRAJECTA_PHOENIX_SAMPLE_RATE", "1"))
            except ValueError:
                sampling = 1.0
            if not 0.0 <= sampling <= 1.0:
                sampling = 1.0
            provider = register(
                sampler=ParentBased(TraceIdRatioBased(sampling)),
                project_name=os.getenv("TRAJECTA_PHOENIX_PROJECT", "trajecta"),
                endpoint=endpoint,
                protocol="http/protobuf",
                auto_instrument=False,
                batch=True,
                verbose=False,
            )
            _provider = provider
            # Instrument only LangChain. Auto-instrumenting every installed
            # SDK can export arbitrary request payloads from unrelated tools.
            # Force privacy settings regardless of optional SDK env defaults.
            LangChainInstrumentor().instrument(
                tracer_provider=provider,
                config=TraceConfig(
                    hide_inputs=True,
                    hide_outputs=True,
                    hide_input_messages=True,
                    hide_output_messages=True,
                    hide_input_text=True,
                    hide_output_text=True,
                    hide_input_images=True,
                    hide_embeddings_vectors=True,
                    hide_embeddings_text=True,
                    hide_llm_invocation_parameters=True,
                    hide_llm_tools=True,
                    hide_prompts=True,
                    hide_choices=True,
                ),
            )
            logger.info("Phoenix AI tracing enabled")
            return True
        except Exception:
            logger.exception("Phoenix instrumentation unavailable; continuing without AI tracing")
            if _provider is not None:
                try:
                    _provider.shutdown()
                except Exception:
                    logger.debug("Phoenix provider cleanup failed", exc_info=True)
                _provider = None
            return False


def shutdown_observability() -> None:
    """Flush buffered spans at FastAPI shutdown (safe when disabled)."""
    global _provider
    with _lock:
        provider = _provider
        _provider = None
        if provider is None:
            return
        try:
            from openinference.instrumentation.langchain import LangChainInstrumentor
            LangChainInstrumentor().uninstrument()
        except Exception:
            logger.debug("Failed to detach LangChain instrumentation", exc_info=True)
        try:
            provider.shutdown()
        except Exception:
            logger.debug("Failed to flush Phoenix spans", exc_info=True)
