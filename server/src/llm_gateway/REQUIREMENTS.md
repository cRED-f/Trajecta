# LLM Gateway (Bifrost) Requirements

## Overview

**Bifrost is an HTTP sidecar** — a separate Go service. The Python server
never imports Go code; it talks to Bifrost over HTTP:

```
LLM calls  → HTTP → http://localhost:8080/v1    (OpenAI-compatible)
MCP calls  → HTTP → http://localhost:8080/mcp   (via langchain-mcp-adapters)
```

Bifrost handles provider routing, failover, caching, and rate limiting.
Provider credentials live **inside Bifrost's config**, not the Python app.

HTTP is the language boundary. Python and Go don't know anything about each other.

## Architecture

```text
Python Deep Agents app
   │   model ──────────┐
   │   MCP tools ────┐ │
   └─────────────────│─│──────────┐
                     │ │  HTTP    │
                     ▼ ▼          │
                  Bifrost (Go :8080)
                     │   │
              /v1=LLM│   │/mcp=MCP
                     │   │
              OpenAI/    GitHub MCP
              Anthropic  Browser MCP
              Ollama     DB MCP
```

## Why Client Instantiation Is Trivial

- One virtual key (`sk-bf-...`) authenticates both `/v1` and `/mcp`.
- Model IDs can be provider-qualified (e.g. `openai/gpt-4o-mini`, `anthropic/claude-sonnet-4-6`) or bare names resolved by a compatible Bifrost model catalog or routing rule.
- `base_url` in the Python app is `http://localhost:8080/v1` (not `.../v1/`).

## Supported Providers (configured in Bifrost)

| Provider | Use Cases |
|----------|-----------|
| OpenAI | General purpose, coding, reasoning |
| Anthropic | Complex planning, coding, reasoning |
| Ollama | Local inference — classification, routing, memory extraction, lightweight transforms |
| OpenRouter/OmniRoute/9Router | Provider routing and aggregation |
| OpenAI-compatible | Generic adapter for additional providers and local gateways |

## Python Responsibilities (this package)

The Python side stays minimal — no Go imports, no per-provider SDK calls
in production:

- `LLMClient` ABC — uniform interface (`complete`/`stream`/`acomplete`/`astream`)
  for local dev and Bifrost-fallback scenarios.
- `OpenAICompatClient` — the ONLY production client. Points at Bifrost `/v1`.
- `AnthropicClient` — direct Anthropic SDK client, used when Bifrost is not running.
- `Router` + `RouteRule` — resolves model strings To a provider; in Bifrost mode
  everything routes through the single Bifrost client.
- `UsageTracker` — aggregates token/cost usage per provider and per task.

## File Map

```
server/src/llm_gateway/
├── REQUIREMENTS.md
├── __init__.py              # re-exports get_gateway, LLMClient, etc.
├── clients.py               # LLMClient ABC, Usage, CompletionResult, CompletionEvent
├── gateway.py               # orchestrator — builds Router from config
├── verify.py                # runnable self-check (no network, no keys)
├── providers/
│   ├── __init__.py
│   ├── openai_compat.py     # OpenAI-compatible client → Bifrost /v1 (production)
│   └── anthropic.py         # Anthropic SDK client (local / non-Bifrost)
└── routing/
    ├── __init__.py
    ├── router.py            # provider selection + fallback chains
    └── usage.py             # token usage tracking and reporting
```

Config lives in `config/default.yaml` under `llm:`:

```yaml
llm:
  default_provider: "bifrost"
  providers:
    bifrost:
      enabled: false            # enable to route through Bifrost
      base_url: "http://127.0.0.1:8080"
    openai:                     # direct-mode fallback (Bifrost off)
      enabled: true
      model: "openai:gpt-5.5"
    anthropic:
      enabled: false
      model: "anthropic:claude-sonnet-4-6"
```

Bifrost's own config lives in `config/bifrost/config.yaml` (providers ▲ keys,
virtual keys). Run Bifrost as a local executable or use an external gateway.

## Bifrost Integration ("HTTP sidecar")

### 1. Start Bifrost

```bash
bifrost serve                # if Bifrost is installed and the CLI provides serve
```

- Bifrost UI:     `http://localhost:8080`
- Inference:      `http://localhost:8080/v1`
- MCP:            `http://localhost:8080/mcp`

### 2. Connect Deep Agents → Bifrost for LLMs

```python
from langchain_openai import ChatOpenAI

model = ChatOpenAI(
    model="openai/gpt-4o-mini",
    base_url="http://localhost:8080/v1",
    api_key=os.environ["BIFROST_VIRTUAL_KEY"],
)
```

This is the whole trick: `base_url` points at Bifrost, so the call chain is

```
Python → Bifrost → OpenAI/Anthropic/...
```

instead of Python → OpenAI directly. Bifrost handles provider routing/failover.

### 3. Connect Python → Bifrost MCP

```python
from langchain_mcp_adapters.client import MultiServerMCPClient

mcp_client = MultiServerMCPClient({
    "bifrost": {
        "transport": "http",
        "url": "http://localhost:8080/mcp",
        "headers": {"Authorization": f"Bearer {os.environ['BIFROST_VIRTUAL_KEY']}"},
    }
})
tools = await mcp_client.get_tools()
```

`tools` are ordinary LangChain tools; the Deep Agent doesn't know they
originated from GitHub/Postgres/Browser MCP servers behind Bifrost.

## Running Verification

```bash
python -m server.src.llm_gateway.verify
```

Exercises `complete`, `stream` (token events + usage recorded), fallback chains,
default provider resolution, and Anthropic system-message splitting — all with
fakes, no API keys or network.

## Key Interfaces

- `get_gateway(settings=None) -> Router` — build/singleton access
- `LLMClient.complete(messages, *, model, temperature, max_tokens) -> CompletionResult`
- `LLMClient.stream(...) -> Iterator[CompletionEvent]` (`start`/`token`/`end`)
- `LLMClient.acomplete(...) -> Awaitable[CompletionResult]` (async)
- `LLMClient.astream(...) -> AsyncIterator[CompletionEvent]` (async)
- `Router.complete/stream(..., task_id=None) -> ...` — fallback + usage recording
- `UsageTracker.get_provider_usage/get_task_usage/get_all_usage` — stats
- `MCPGateway.connect()/get_tools()/close()` — Bifrost `/mcp` wrapper