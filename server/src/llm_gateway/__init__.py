"""LLM Gateway (Bifrost) — unified LLM interface with provider abstraction, routing, and usage tracking.

Every subsystem (planner, skill miner, evaluator, memory extraction) consumes this.

Bifrost sidecar mode (production):
    One OpenAICompatClient → Bifrost HTTP → providers. Routing/failover/caching in Go.

Direct provider mode (local dev):
    Individual OpenAICompatClient / AnthropicClient wired per enabled provider.
"""

from server.src.llm_gateway.gateway import get_gateway, reset_gateway
from server.src.llm_gateway.clients import LLMClient, CompletionResult, CompletionEvent, Usage

__all__ = ["get_gateway", "reset_gateway", "LLMClient", "CompletionResult", "CompletionEvent", "Usage"]
