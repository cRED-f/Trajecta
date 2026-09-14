# Server Requirements

## Overview

Python backend running as a Tauri sidecar process. FastAPI provides the HTTP + streaming interface to the desktop UI. LangChain Deep Agents + LangGraph provide the autonomous agent runtime.

## Responsibilities

1. **Agent Execution** — receive tasks from the UI, plan, execute tools, return results
2. **Streaming** — stream agent events (tool calls, reasoning, status) to the desktop via SSE
3. **Memory Management** — read/write all four memory types
4. **Skill Learning** — trajectory capture, skill mining, evaluation, versioning, promotion
5. **Tool Execution** — built-in tools + MCP integrations, sandboxed where configured
6. **Guardrails** — input/output validation, permission enforcement, risk classification
7. **LLM Routing** — route model calls through Bifrost to configured providers
8. **Observability** — emit OpenTelemetry traces, metrics, and logs
9. **Configuration** — manage provider keys, tool configs, guardrail rules, user preferences

## Tech Stack

- **Python 3.11+**
- **FastAPI** — HTTP + SSE streaming
- **Pydantic v2** — schemas, validation, settings
- **LangChain Deep Agents** — agent runtime
- **LangGraph** — stateful execution graphs
- **Uvicorn** — ASGI server
- **SQLite** — structured state + FTS
- **Qdrant (local)** — vector storage
- **OpenTelemetry SDK** — instrumentation

## File Map

```
server/
├── src/
│   ├── api/              # FastAPI app, routes, middleware
│   ├── agent/            # Deep Agents runtime
│   ├── memory/           # Multi-type memory system
│   ├── skills/           # Skill learning pipeline
│   ├── tools/            # Built-in tools, MCP, sandbox
│   ├── guardrails/       # Validators, permissions, risk
│   ├── llm_gateway/      # Bifrost provider abstraction
│   ├── observability/    # OpenTelemetry setup
│   ├── config/           # Settings and env loading
│   ├── models/           # Pydantic schemas
│   ├── utils/            # Shared helpers
│   └── main.py           # Server entry point
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── evals/
│   └── fixtures/
├── pyproject.toml
└── REQUIREMENTS.md
```

## API Contract (Stubs)

All routes are prefixed `/api/v1`. Streaming endpoints use Server-Sent Events.

| Method | Endpoint | Purpose |
|--------|----------|---------|
| POST | /tasks | Submit a new task to the agent |
| GET | /tasks/{id} | Get task status and result |
| GET | /tasks/{id}/stream | SSE stream of agent events for a task |
| GET | /tasks | List tasks |
| GET | /skills | List registered skills |
| GET | /skills/{id} | Get skill detail + versions |
| POST | /skills/{id}/rollback | Rollback to a previous skill version |
| GET | /memory/{type} | Query memory (working/episodic/semantic/procedural) |
| GET | /tools | List configured tools |
| GET | /models | List configured model providers |
| POST | /models | Add/update model provider config |
| GET | /traces | Recent agent traces |
| GET | /health | Health check |
