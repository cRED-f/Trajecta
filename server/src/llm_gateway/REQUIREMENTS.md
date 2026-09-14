# LLM Gateway (Bifrost) Requirements

## Overview

Bifrost provides a unified LLM interface with provider abstraction, routing, fallback, retries, rate-limit handling, and token usage tracking. Trajecta routes all model calls through Bifrost.

## Supported Providers

| Provider | Use Cases |
|----------|-----------|
| OpenAI | General purpose, coding, reasoning |
| Anthropic | Complex planning, coding, reasoning |
| Ollama | Local inference — classification, routing, memory extraction, tool ranking, lightweight transforms |
| OmniRoute | Provider routing and aggregation |
| 9Router | Provider routing and aggregation |
| OpenAI-compatible | Generic adapter for additional providers and local gateways |

## Responsibilities

- Provider abstraction (uniform interface for all providers)
- Routing (direct requests to the correct provider)
- Fallback (failover to alternate provider on error)
- Retries (exponential backoff on transient failures)
- Rate-limit handling (respect provider rate limits)
- Token usage tracking (tokens per request, per task, per session)
- Model configuration (provider keys, model selection, parameters)

## File Map

```
server/src/llm_gateway/
├── REQUIREMENTS.md
├── __init__.py
├── gateway.py            # Bifrost gateway — unified interface
├── providers/
│   ├── __init__.py
│   ├── base.py           # Abstract provider interface
│   ├── openai.py         # OpenAI provider
│   ├── anthropic.py      # Anthropic provider
│   ├── ollama.py         # Ollama provider (local)
│   ├── omni_route.py     # OmniRoute provider
│   ├── nine_router.py    # 9Router provider
│   └── openai_compat.py  # Generic OpenAI-compatible adapter
└── routing/
    ├── __init__.py
    ├── router.py          # Provider selection and routing logic
    ├── fallback.py        # Fallback chain configuration
    └── usage.py           # Token usage tracking and reporting
```

## Key Interfaces

- `complete(model, messages, **kwargs) -> Response` — single completion
- `stream(model, messages, **kwargs) -> AsyncIterator[Chunk]` — streaming completion
- `select_provider(task_type) -> Provider` — route to best provider for task
- `get_usage(task_id) -> UsageStats` — token and cost stats
- `list_providers() -> list[ProviderConfig]` — configured providers
