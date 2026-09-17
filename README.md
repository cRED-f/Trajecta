# Trajecta

**Local-first autonomous desktop agent that learns reusable skills from successful task trajectories.**

Trajecta captures how tasks were solved, identifies repeated successful patterns, converts those patterns into reusable skills, evaluates those skills against previous and held-out tasks, and only promotes them when they measurably improve future performance.

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Desktop Shell | Tauri 2 |
| UI | React + TypeScript |
| Backend | Python + FastAPI |
| Agent Runtime | LangChain Deep Agents + LangGraph |
| LLM Gateway | Bifrost (OpenAI, Anthropic, Ollama, OmniRoute, 9Router) |
| Guardrails | Guardrails AI + Custom Policy Engine |
| Memory | SQLite + FTS + Qdrant (local vector store) |
| Observability | OpenTelemetry + Grafana (Tempo, Loki, Prometheus) |
| Sandboxing | Docker |

## Project Structure

```
trajecta/
├── apps/desktop/          # Tauri + React + TypeScript desktop app
├── packages/              # Shared TypeScript packages
│   ├── core/
│   ├── ts-sdk/
│   └── shared-types/
├── server/                # Python FastAPI backend (sidecar)
│   └── src/
│       ├── api/           # FastAPI routes + middleware
│       ├── agent/         # Deep Agents runtime, planner, context, subagents
│       ├── memory/        # Working, episodic, semantic, procedural memory
│       ├── skills/        # Trajectory store, miner, evaluation, versioning
│       ├── tools/         # Built-in tools, MCP, sandbox
│       ├── guardrails/    # Validators, permissions, risk classification
│       ├── llm_gateway/   # Bifrost provider routing
│       ├── observability/ # OpenTelemetry instrumentation
│       ├── config/        # Application configuration
│       ├── models/        # Pydantic schemas
│       └── utils/         # Shared utilities
├── config/                # Environment and deployment configs
├── infra-docker/          # Dockerfiles and compose for sandboxing + observability
├── evals/                 # Evaluation benchmarks, suites, results
├── scripts/               # Dev and utility scripts
├── docs/                  # Project documentation
├── skills/                # User-visible skill definitions
└── .trajecta/             # Local agent state
```

## Quick Start

> **Not yet implemented.** This repository currently contains the project specification and folder structure.

## Documentation

- [Project Spec](Trajecta_Project_Spec.md)
- [Server Requirements](server/REQUIREMENTS.md)
- [Desktop Requirements](apps/desktop/REQUIREMENTS.md)
- [Memory System](server/src/memory/REQUIREMENTS.md)
- [Skill Learning System](server/src/skills/REQUIREMENTS.md)
- [Guardrails](server/src/guardrails/REQUIREMENTS.md)
- [LLM Gateway](server/src/llm_gateway/REQUIREMENTS.md)
- [Observability](server/src/observability/REQUIREMENTS.md)
- [Evaluation System](evals/REQUIREMENTS.md)
- [Tool Runtime](server/src/tools/REQUIREMENTS.md)
- [Docker & Sandboxing](infra-docker/REQUIREMENTS.md)
