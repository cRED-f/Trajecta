# Trajecta

**Local-first autonomous desktop agent that learns reusable skills from successful task trajectories.**

Trajecta is a chat-first autonomous desktop agent built on a local Tauri + React client and a Python `FastAPI` sidecar. It captures how tasks are solved end-to-end, mines repeated successful patterns into portable, versioned **skills**, verifies those skills by replaying them against captured fixtures, evaluates them against baseline and held-out tasks, and only promotes them into production when they measurably improve future performance.

Because everything runs locally-first, you keep full control: your own LLM gateway (Bifrost / OpenAI / Anthropic / Ollama / 9Router), your own memory, your own tools, and a deterministic permission policy that decides exactly what the agent may do — no cloud lock-in for the runtime.

---

## Table of Contents

- [Features](#features)
- [How Skill Learning Works](#how-skill-learning-works)
- [Architecture](#architecture)
- [Tech Stack](#tech-stack)
- [Repository Layout](#repository-layout)
- [API Surface](#api-surface)
- [Configuration](#configuration)
- [Getting Started](#getting-started)
- [Testing & Verification](#testing--verification)
- [Documentation](#documentation)
- [Roadmap / Status](#roadmap--status)

---

## Features

### Agent runtime

- **Chat-first autonomous harness** built on LangChain Deep Agents + LangGraph, streaming responses over server-sent events (SSE) with heartbeats.
- **Preflight-safe streaming**: all validations that can fail happen before SSE headers are sent, so a failed turn never corrupts an open stream.
- **Conversation branching**: edit / resend / regenerate run on LangGraph checkpoint-forked threads with branch-local thread IDs, so each interaction rewinds to a real checkpoint instead of re-running blind.
- **Human-in-the-loop approvals**: sensitive actions pause for explicit approve / edit / reject / respond decisions, resuming from a persisted checkpoint.

### Deterministic permission engine

- Six user-controllable capability domains (filesystem-write, terminal, browser-actions, computer-control, external-communication, network-mutations), each with an **allow / ask / deny** mode.
- **deny** removes the tool completely from the next agent run; **ask** keeps the tool but gates it behind an approval; **allow** executes freely.
- Decisions persist in SQLite (`agent_permissions` / `agent_settings`) and are applied per-run, so changing a permission changes the actual tool surface the model sees — settings are not cosmetic.

### Memory system

- Four memory tiers: **working** (in-run context), **episodic** (conversation traces), **semantic** (durable user facts), and **procedural** (verified skills).
- Hybrid retrieval: SQLite **FTS5** lexical search + embedded **Qdrant** vector search, with canonical facts duplicated into Deep Agents' `/memories/` store.
- Automatic memory capture can be toggled on/off; memories are browsable and deletable from the Settings UI.

### Verified skill learning

Trajecta's core loop — a full _mine → verify → evaluate → promote → version_ pipeline (see [How Skill Learning Works](#how-skill-learning-works)).

### Tools & integrations

- A large built-in personal toolset: memory, session recall, tasks, **scheduled autonomous jobs** (once / interval / cron), in-app notifications, web search/extract/RSS, document reading (PDF, DOCX, XLSX, PPTX, CSV, HTML…), OCR, image transforms, archives, local media conversion / transcription / speech, desktop control (opt-in), SQLite queries, deterministic utilities, and long-running host process management.
- **MCP support**: servers and individual tools can be enabled/disabled; tools are dynamically discovered and verified on connect.
- **Sandboxed execution** via an isolated Docker sandbox (deep-agents `execute`), with host process tools restricted to approved, long-running processes.

### Desktop UI

- Tauri 2 shell with a React 19 + TypeScript client.
- Full chat view with streaming, branching, approvals, attachment chips, and RAG-backed document Q&A.
- A **Settings control center**: General, Permissions, Memory, Skills, Scheduled Tasks, and MCP Tools — every page wired to live backend data.

---

## How Skill Learning Works

1. **Capture.** Each task run records a _trajectory_ — the user request, tool calls, and outcomes — plus an initial-state _replay fixture_ (hashes/copies of the workspace and attachments the task depended on).
2. **Mine.** Repeated successful patterns are mined from trajectories into candidate skills (a semantic skill bundle: `SKILL.md` instructions, `workflow.yaml`, `metadata.json`, and `eval.yaml`).
3. **Verify.** Candidates are verified for correct bundle format and safe tool-execution semantics before they are allowed to be evaluated.
4. **Evaluate.** A candidate is replayed against the captured fixtures and evaluated against baseline performance, producing a pass/fail verdict with metrics.
5. **Promote.** Only passing, verified candidates are promoted into production through a versioned pipeline (`SkillVersioner` → `SkillPromoter`), versioned and stored under `/skills/`. Every promotion is revertible.
6. **Operate.** Processed skills become _procedural memory_ the agent can load and search, and the whole lifecycle is exposed via REST (evaluate / promote / reject / enable / disable).

---

## Architecture

Trajecta is best understood not as a client—server app but as a **self-improving agent system**: a deterministic, human-governed control plane wrapped around an autonomous reasoning loop that continuously converts its own task-completion history into verified, versioned procedures (skills) that change how it subsequently behaves. Three concerns are deliberately separated:

1. **Execution** — doing: online, streaming, human-in-the-loop task solving;
2. **Learning** — improving: an offline, batched pipeline that turns trajectories into skills *only on evidence*;
3. **Control** — governing: the deterministic permission policy that projects user intent onto the runtime tool surface.

These three loops share one state substrate and one FastAPI process; the Tauri shell is a presentational client that never holds authority. The sidecar is the single source of truth for conversations, memory, skills, permissions, schedules, and traces, exposed over one HTTP/JSON contract (JSON for commands, SSE for the streaming channel).

### Layer map — nine planes of separation

Dependency flows **strictly downward and forward**: each layer calls only beneath itself, and only Layers 1–2 are reachable by a user.

```
  ┌────────────────────────────────────────────────────────────────────────────┐
  │ L1  PRESENTATION            Tauri 2 shell · React 19 · TypeScript          │
  │                             React Query (server state) · Zustand (UI)      │
  │                             chat · settings · memory · skills · monitors   │
  └──────────────────────────────────────┬─────────────────────────────────────┘
                                         │
  ┌──────────────────────────────────────▼─────────────────────────────────────┐
  │ L2  TRANSPORT                 HTTP/JSON (commands) · SSE (stream channel)  │
  │                             deltas · tool_call · approvals · heartbeats    │
  └──────────────────────────────────────┬─────────────────────────────────────┘
                                         │
  ┌──────────────────────────────────────▼─────────────────────────────────────┐
  │ L3  API / EDGE                FastAPI · /api/v1 · routers                  │
  │                             chat · memory · permissions · skills · tasks   │
  │                             tools/mcp · models · traces · health           │
  └──────────────────────────────────────┬─────────────────────────────────────┘
                                         │
  ┌──────────────────────────────────────▼─────────────────────────────────────┐
  │ L4  APPLICATION SERVICES       ChatService      SkillsService              │
  │                             prepare_message    (repository + eval +        │
  │                             stream_prepared     promote + version)         │
  │                             ConnectorVerification      Scheduler           │
  └──────────────────────────────────────┬─────────────────────────────────────┘
                                         │
  ┌──────────────────────────────────────▼─────────────────────────────────────┐
  │ L5  AGENT ORCHESTRATION        DeepAgentRuntime · Deep Agents loop         │
  │                             create_deep_agent() · LangGraph                │
  │                             checkpointer · store · branches                │
  │                             HITL interrupts · execute backend selection    │
  └──────────────────────────────────────┬─────────────────────────────────────┘
                                         │
  ┌──────────────────────────────────────▼─────────────────────────────────────┐
  │ L6  CAPABILITY & POLICY         PersonalToolProvider (~70 tools)           │
  │                             PermissionPolicyStore (allow · ask · deny)     │
  │                             TOOL_PERMISSION_GROUPS (6 domains → ~30 tools) │
  │                             MCP registry · tool preferences                │
  └──────────────────────────────────────┬─────────────────────────────────────┘
                                         │
  ┌──────────────────────────────────────▼─────────────────────────────────────┐
  │ L7  MEMORY & KNOWLEDGE          working · episodic · semantic · procedural │
  │                             hybrid recall: FTS5 lexical ↕ Qdrant vector    │
  │                             trajectory store · replay fixtures             │
  │                             skill bundles / skills & / memories/ projection│
  └──────────────────────────────────────┬─────────────────────────────────────┘
                                         │
  ┌──────────────────────────────────────▼─────────────────────────────────────┐
  │ L8  DATA ACCESS & PERSISTENCE    SQLite trajecta.db · SQLite langgraph.db  │
  │                             SQLite FTS5 · embedded Qdrant · filesystem     │
  │                             versioned migrations (currently v11)           │
  └──────────────────────────────────────┬─────────────────────────────────────┘
                                         │
  ┌──────────────────────────────────────▼─────────────────────────────────────┐
  │ L9  EXTERNAL INFRASTRUCTURE      Bifrost LLM gateway (openai · anthropic · │
  │                             ollama · 9Router · omni_route)                 │
  │                             Docker sandbox (execute) · MCP servers         │
  └────────────────────────────────────────────────────────────────────────────┘
```

| Layer | Plane | Owns | Speaks to |
| ----- | ----- | ---- | --------- |
| L1 | Presentation | UI components, client state, SSE consumption | L2 via `request()` / EventSource |
| L2 | Transport | the HTTP/JSON + SSE contract, stream framing | L3 routers |
| L3 | API / Edge | route modules, request validation, error → status mapping | L4 services |
| L4 | Application Services | chat/skills orchestration, preflight, verification, scheduling | L5 + L6 |
| L5 | Agent Orchestration | Deep Agents + LangGraph loop, checkpoints, branches, HITL, backend choice | L6 tools + L7 memory + LLM |
| L6 | Capability & Policy | tool registry, permission domains, policy projection, MCP | L7 (memory tools) + L9 (sandbox/LLM) |
| L7 | Memory & Knowledge | 4-tier store, hybrid retrieval, trajectories, skill bundles | L8 |
| L8 | Data Access | SQLite, FTS5, Qdrant, filesystem, migrations | persistence |
| L9 | External | LLM providers, sandbox, external MCP tools | — |

Rules the layering enforces: L4–L8 are unreachable from outside except through L3; L6 is the *only* layer that may mutate the tool surface; L7 is the *only* place writes land before persistence; and each layer drops its contract behind the one below (a user never reaches L5, the LLM never reaches L6 policy, and no layer above L8 speaks to storage directly).

### 1 · The three coupled loops

```
                        ┌──────────────────────────────────────────────┐
                        │ 3 · CONTROL  guardrails/policy.py            │
                        │   allow · ask · deny  →  filter_tools()      │
                        │   interrupt_on() → HITL → backend_swap()     │
                        └───────────────────────┬──────────────────────┘
                                 policy snapshot taken per run
                                                 ▼
   user ──▶ ┌───────────────────────────────────────────────────────────┐
            │ 1 · EXECUTION (online)                                    │
            │   preflight → DeepAgentRuntime.create_deep_agent()        │
            │   tools(filtered) · permissions · interrupt_on            │
            │   LangGraph: checkpoint ─ tool_call ─ execute ─ checkpoint│
            │   SSE stream · branch / edit / resend / regenerate        │
            └───────────────┬───────────────────────────┬───────────────┘
                            │ trajectory + fixture      │ procedural skills
                            ▼                           ▲ (loaded each run)
            ┌───────────────────────────────────────────────────────────┐
            │ 2 · LEARNING (offline, evidence-gated)                    │
            │   mine → verify → replay-fixture → evaluate → promote → vn│
            │   promotion ONLY when evaluation passes vs. baseline      │
            └───────────────────────────────────────────────────────────┘
```

| Loop | Recency | Cadence | Consumes | Produces |
| ---- | ------- | ------- | -------- | -------- |
| 1 · Execution | online | every user turn / scheduled job | filtered tools, policy snapshot, procedural skills | response, trajectory, replay fixture |
| 2 · Learning | offline | batched / on-demand | trajectories + fixtures | versioned, verified skills |
| 3 · Control | offline | instant (persisted policy) | permission modes, settings | filtered tool surface, interrupt policy |

The loops are **feed-forward coupled**: Execution emits evidence (trajectories, fixtures); Learning converts evidence into verified skills; Control gates what Execution may touch. The only path from real-world behavior to persistent skill is through Evidence → Verification → Evaluation — nothing is promoted by fiat.

### 2 · The execution loop, in depth

A live agent run is a walk over an explicit state tuple realized in LangGraph:

```
S = ⁃ conversation id, branch id, checkpoint ⁃
     ⁃ tool surface  = T_user ∩ T_domain(policy)    (DENY tools absent)
     ⁃ permissions   = projected FilesystemPermissions
     ⁃ interrupt     = {tool → approved decisions | mode == ASK}
     ⁃ backend       = CompositeBackend (execute iff terminal ≠ deny)
     ⁃ store         = /memories/, /skills/   (canonical facts dual-written)
```

Each turn is a *checkpointed transaction*:

1. **Preflight** (`prepare_message`) — every validation that can fail — attachment checks, size limits, model resolution, policy read — happens *before* the SSE headers are sent. The stream is opened only into a run already known to be viable, so a failed turn can never leave a client with a half-formed stream.
2. **Runtime assembly** (`DeepAgentRuntime.prepare`) — the policy is snapshotted once per run, the DENY-filtered surface is built, `memory_save` is dropped when automatic memory is disabled, and `create_deep_agent()` receives `permissions`, `interrupt_on`, and the backend together. The configuration a run starts with is the configuration it keeps.
3. **Loop** — the model cycles *reason → tool_call → execute → checkpoint* inside Deep Agents. Each step lands on a LangGraph checkpoint, giving three safety-relevant properties:
   - **Convergence** — a run is resumable from any recorded checkpoint;
   - **Rewind / branch** — edit, resend, and regenerate fork a new thread from a checkpoint with a branch-local thread ID; they replay the *actual* state, never a re-approximation;
   - **HITL** — an ASK-domain tool raises an interrupt; the runtime streams an approval event and persists the checkpoint, and the human's approve / edit / reject / respond decision resumes exactly there.
4. **Emission** — the run writes back: response events over SSE (deltas, tool-call events, heartbeats) plus a **trajectory** and an initial-state **replay fixture** to the trajectory store. Tool receipts are captured with sensitive fields redacted.

Because DENY is applied at tool-construction time and ASK at invocation time, the *composition* a run sees is a direct projection of policy — permissions are not a post-hoc permission check layered on a fixed toolset.

### 3 · Memory hierarchy as cognitive state

The memory layer is a four-tier hierarchy with explicit consolidation semantics, backed by a hybrid lexical/vector index:

| Tier | Subsystem | Physical backing | Write path | Read path |
| ---- | --------- | ---------------- | ---------- | --------- |
| working | short-term store | app memory / session | in-run context | immediate, highest precedence |
| episodic | `memory/episodic` | SQLite | per-conversation traces | conversation recall |
| semantic | `memory/semantic` | SQLite + **FTS5** + **embedded Qdrant** | durable facts (auto or manual) | hybrid search: lexical ∪ vector, reranked |
| procedural | `memory/procedural` | filesystem skill bundles + registry | **only via promotion** | skill load / search |

Two properties matter:

- **Hybrid recall.** Semantic retrieval queries FTS5 lexically and Qdrant semantically and merges the results, so exact identifiers and paraphrase matches each have a reliable path.
- **Dual-write with a working subset.** Canonical facts are written to the application DB (the record of truth) *and* mirrored into the Deep Agents `store` under `/memories/`, `/skills/` — the narrow, serialized subset the model actually reads each run. The store is a projection, not a second source of truth; the app DB owns the full record, which is why memory editing/deletion from the UI takes effect immediately and deterministically.

### 4 · The learning loop: trajectory → verified skill

The provenance chain is the core research contribution: behavior is captured, distilled, *proven against its own history*, and only then granted procedural status.

```
capture → mine → verify → replay-evaluate → promote → version → procedural
   │        │        │             │            │         │            │
   │   trajectory    │             │            │         │            │
   │   store         │             │            │         │            │
   └── duplicates ───┘             │            │         │            │
                                   │            │         │            │
                 SKILL.md / workflow.yaml / metadata.json / eval.yaml    │
                                   │            │         │            │
                    format + tool  │            │         │            │
                    verification   │            │         │            │
                                    └──────────►│         │            │
                      fixtures captured at run time  │         │        │
                                                    │         │        │
                   evaluated vs. baseline,          │         │        │
                   replayed against fixtures  ◄─────┘         │        │
                                                                │        │
                    only a PASSING skill may reach this point ◄─┘        │
                                                                │        │
                                        full-bundle versioning ◄───────┘  │
                                                                │        │
                                             procedural load ◄───────────┘
```

The gates are deliberately **deterministic and reproducible**:

- **Verification is structural** — a candidate must be a well-formed semantic bundle and use only tool-execution semantics the verifier accepts; malformed candidates cannot enter evaluation.
- **Evaluation is empirical, not judgmental** — a candidate is replayed against the captured initial-state fixtures by the replay executor and scored against baseline (and held-out) performance. The verdict is a pass/fail over measured metrics, not an LLM's opinion.
- **Promotion is the only write path into procedural memory** — `SkillPromoter` + `SkillVersioner` produce versioned bundles (a promote records `version_id` and `previous_version`), and every promotion is revertible. Disabling a skill removes it from the procedural surface; re-enabling re-materializes its active version, so enable/disable is exact, not lossy.
- **Every stage is observable and auditable** via the REST surface (candidates → evaluations → fixtures → versions), and scheduled autonomous jobs feed the *same* trajectory store — so unattended work also accumulates evidence.

### 5 · The control plane: deterministic governance

The permission engine is a pure, total function over capability domains:

```
perm : domain → { allow, ask, deny },   domain ∈ 6 named capability domains
```

mapped onto ~30 named tools by `TOOL_PERMISSION_GROUPS`. Enforcement is **two-point**, guaranteeing that policy cannot be bypassed by a merely reluctant model:

- **Construction time** — `filter_tools()` removes every personal tool whose domain is `deny`; with `terminal = deny` the runtime also swaps the default backend for `_backend_without_execute()`, so the `execute` tool does not exist in that run's world at all.
- **Invocation time** — `ask` domains surface as `interrupt_on` entries; a locked domain executes only with a human's approved decision.

The control plane is *separate from intent*: it reads the persisted `agent_permissions` / `agent_settings` tables into an immutable per-run snapshot, deliberately *not* subjecting the capability boundary to the model under test. MCP/server tool enablement is governed by a separate preference store, keeping provider-specific toggles distinct from capability policy.

### 6 · Consistency, safety & failure semantics

Invariants that hold across the runtime:

| # | Invariant | Mechanism |
| - | --------- | --------- |
| I1 | No stream is opened into a failing run | all preflight validations precede SSE headers |
| I2 | Any run state is resumable / rewindable | checkpoint inserted before every executed step; branching forks real checkpoints |
| I3 | No behavior becomes a skill without evidence | evaluation is gated by replay against captured fixtures vs. baseline |
| I4 | The only write path into procedural memory is promotion | `agent_permissions` enforced at tool construction and invocation |
| I5 | Secrets never persist in tool receipts | `redact()` on credential-keyed fields at capture |
| I6 | The capability boundary is never delegated to the LLM | deterministic policy function, per-run immutable snapshot |
| I7 | State is convergent across stores | app DB is authoritative; LangGraph store is a mirroring projection |
| I8 | Schema evolves safely | sequential, versioned migrations (currently v11) run on open |

**Concurrency model.** The sidecar is a single asyncio process; runs are interleaved, scheduler-driven jobs share the same runtime and policy snapshots, and the scheduler (polling `due_schedules()`) merely enqueues full agent runs — job execution is the same Deep Agents harness as interactive chat, which is why scheduled work and chat work produce identically structured evidence.

**Non-goals (honest scope).** Trajecta does *not* do online/in-context reinforcement learning over live feedback; improvement is a batched, offline, evidence-gated pipeline. It does not gate capabilities through an LLM validation layer — Guardrails AI is deferred in favor of the deterministic policy engine. It is not distributed: one sidecar process, local storage, local-first by design.

### 7 · Runtime topology

```
┌─────────────┐   HTTP/JSON + SSE   ┌─────────────────────────────────────────┐
│ Tauri shell │ ◀─────────────────▶ │ FastAPI sidecar (single asyncio proc)    │
│ apps/desktop│  127.0.0.1:8420     │  chat·memory·skills·permissions·tasks    │
│ React 19    │                     │  tools/mcp · models · traces · health    │
└─────────────┘                     └───────┬──────────────┬──────────────────┘
                                            │              │
              ┌─────────────┬───────────────┼───────┐      │
              ▼             ▼               ▼       ▼      ▼
      ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌────────────┐
      │ Bifrost    │ │ SQLite     │ │ Qdrant     │ │ Docker     │ │ MCP        │
      │ LLM gateway│ │ trajecta.db│ │ embedded   │ │ sandbox    │ │ servers    │
      │ + providers│ │ langgraph  │ │ vector     │ │ (execute)  │ │ (external) │
      │ OpenAI/…   │ │ FTS5       │ │            │ │            │ │            │
      └────────────┘ └────────────┘ └────────────┘ └────────────┘ └────────────┘
```

The shell is disposable by design — it renders server state (React Query) and local UI state (Zustand) only. Terminate the sidecar and there is nothing ephemeral left in charge: every durable artifact lives under the FastAPI process's local stores.

---

## Tech Stack

| Layer         | Technology                                                                              |
| ------------- | --------------------------------------------------------------------------------------- |
| Desktop Shell | Tauri 2 (Rust WebView shell)                                                            |
| UI            | React 19 + TypeScript + Vite, React Query, Zustand, `lucide-react`                      |
| Backend       | Python `^3.11`, FastAPI, Uvicorn, Pydantic v2                                           |
| Agent Runtime | LangChain Deep Agents `^0.7.13`, LangGraph `^1.2`, LangGraph SDK                        |
| LLM Gateway   | Bifrost HTTP sidecar (OpenAI, Anthropic, Ollama, OmniRoute, 9Router providers)          |
| Guardrails    | Deterministic permission engine (`guardrails/policy.py`) + Guardrails AI                |
| Memory        | SQLite (aiosqlite) + FTS5 + embedded Qdrant vector store                                |
| Skills        | Verified pipeline: trajectories, replay fixtures, replay executor, evaluator, versioner |
| Tools         | Built-in personal toolset, MCP via `langchain-mcp-adapter`, Docker sandbox              |
| Observability | OpenTelemetry SDK + OTLP exporters + FastAPI instrumentation                            |
| Packaging     | Poetry (`pyproject.toml`), pnpm workspace, Docker Compose (Bifrost + Qdrant)            |

> Guardrails AI is deliberately deferred at the runtime level: the enforcement path for tool capabilities is the deterministic policy engine, not an LLM-based validator.

---

## Repository Layout

```
trajecta/
├── apps/
│   └── desktop/              # Tauri 2 + React + TypeScript client
│       ├── src/              #   components/, hooks/, lib/, stores/, types/, styles/
│       │   └── components/settings/   # Settings control-center pages
│       └── src-tauri/        #   Tauri Rust shell configuration
├── packages/                 # Shared TypeScript packages (core, ts-sdk, shared-types)
├── server/                   # Python FastAPI backend (Tauri sidecar)
│   ├── src/
│   │   ├── api/              #   FastAPI app factory + route modules + middleware
│   │   │   └── routes/       #     chat, memory, permissions, skills, tasks, tools, models, traces, health
│   │   ├── agent/            #   Deep Agents runtime wiring
│   │   ├── chat/             #   service, model factory, RAG, runtime, MCP, repository
│   │   ├── memory/           #   provider + working/episodic/semantic/procedural stores
│   │   ├── skills/           #   repository, mining, replay, evaluation, promotion, versioning
│   │   ├── tools/            #   builtin/, personal/, mcp/, sandbox/, verification/
│   │   ├── guardrails/       #   policy engine (+ validators, risk, permissions)
│   │   ├── llm_gateway/      #   Bifrost provider routing
│   │   ├── config/           #   Settings (Pydantic-settings, config/default.yaml)
│   │   ├── models/           #   Pydantic schemas
│   │   ├── utils/            #   shared utilities
│   │   └── main.py           #   uvicorn entry point
│   └── tests/                # pytest suite
├── config/                   # default.yaml, guardrail-rules.yaml, Bifrost config
├── infra-docker/             # sandbox Dockerfile + LangGraph compose + compose stack
├── docker-compose.yaml       # Bifrost + Qdrant dev stack
├── evals/                    # Evaluation benchmarks and results
├── skills/                   # User-visible skill definitions
├── docs/                     # Project documentation
├── scripts/                  # Dev / utility scripts
└── .trajecta/                # Local agent state (DBs, uploads, skills cache)
```

---

## API Surface

All routes are mounted under `/api/v1`.

| Area        | Routes                                                                                                                                                                                                                                                                                                                              |
| ----------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Chat | `POST/GET /chat/conversations`, `GET /chat/conversations/{id}`, `PUT /chat/conversations/{id}/branches/{id}/activate`, `PUT /chat/conversations/{id}/model`, `POST /chat/conversations/{id}/messages/stream`, `POST /chat/conversations/{id}/messages/{msg_id}/{edit,resend,regenerate}/stream`, `POST /chat/conversations/{id}/attachments`, `GET/POST /chat/conversations/{id}/approval`, `DELETE /chat/conversations/{id}`, `POST /chat/conversations/{id}/cancel` |
| Memory      | `GET /memory` (settings + counts + items), `PATCH /memory/settings`, `GET /memory/semantic/{key}`, `GET/POST /memory/{type}`, `DELETE /memory/{type}/{key}`                                                                                                                                                                         |
| Permissions | `GET /permissions`, `PATCH /permissions/{permission_id}`                                                                                                                                                                                                                                                                            |
| Skills      | `GET /skills` (registry + candidates + summary), `POST /skills/candidates/{id}/evaluate`, `POST /skills/candidates/{id}/promote`, `POST /skills/candidates/{id}/reject`, `GET /skills/candidates/{id}`, `GET /skills/evaluations`, `GET /skills/fixtures/{id}`, `GET /skills/{name}` + `…/versions`, `PATCH /skills/{name}/enabled` |
| Tasks       | `GET/POST /tasks/schedules`, `GET/PATCH/DELETE /tasks/schedules/{job_id}`                                                                                                                                                                                                                                                           |
| Tools / MCP | `GET /tools`, `GET/POST /tools/mcp`, `PATCH /tools/mcp/servers/{server}`, `PATCH /tools/mcp/servers/{server}/tools/{tool}`, `GET /tools/receipts/{id}`                                                                                                                                                                              |
| Models      | `GET /models`                                                                                                                                                                                                                                                                                                                       |
| Traces      | `GET /traces` (OTel traces)                                                                                                                                                                                                                                                                                                         |
| Health      | `GET /health`                                                                                                                                                                                                                                                                                                                       |

---

## Configuration

Configuration lives in `config/default.yaml` and is loaded through `server/src/config` (Pydantic-settings). Key sections:

- **server** — bind host/port (default `127.0.0.1:8420`).
- **llm** — provider selection and credentials. Default provider is `9_router`; you can enable `openai`, `anthropic`, `ollama`, `omni_route`, or a generic `openai_compat` endpoint. The Bifrost HTTP sidecar is expected at `8080` in Docker.
- **memory** — data paths for `trajecta.db`, `langgraph.db`, embedded Qdrant, memory files, and skills path.
- **skills** — storage + replay-fixture capture settings (max files, max sizes, exclude globs).
- **guardrails** — `hitl_enabled`, default risk level, rules file.
- **sandbox** — Docker sandbox image, resource limits, network toggle.
- **chat** — uploads path, default model, upload size limits, stream heartbeat interval.

Environment overrides use the `TRAJECTA_*` prefix (e.g. `TRAJECTA_9_ROUTER_MODEL`).

---

## Getting Started

> Requires: Python 3.11+, Node 18+ (pnpm), Docker (optional, for sandbox + Bifrost/Qdrant).

### 1. Start the backend

```bash
# Create the virtualenv (uv) and install dependencies
uv sync

# Run the FastAPI sidecar
./.venv/Scripts/python.exe -m uvicorn server.src.api.app:app               # dev, from repo root

# or via the module entry point
./.venv/Scripts/python.exe -m server.src.main
```

The server listens on `127.0.0.1:8420` by default (see `server.host`/`server.port`).

### 2. (Optional) Start the local services stack

```bash
docker compose up bifrost qdrant
```

This starts the **Bifrost** LLM gateway sidecar on `:8080` and a **Qdrant** vector alternative on `:6333`. If you instead rely on `9_router`, `openai`, or a local Ollama model for the LLM, you can skip Bifrost.

### 3. Run the desktop app

```bash
cd apps/desktop
pnpm install
pnpm dev          # Vite dev server on http://127.0.0.1:1420
```

To build the full Tauri app:

```bash
cd apps/desktop
pnpm tauri dev    # hot-reloaded desktop shell
pnpm tauri build  # distributable
```

### 4. Configure the LLM

Edit `config/default.yaml` and set the provider you want to use for conversations. The default is `9_router` / `claude-opus-free` (override with `TRAJECTA_9_ROUTER_MODEL`). Restart the backend after editing.

---

## Testing & Verification

**Backend** (from repo root):

```bash
./.venv/Scripts/python.exe -m pytest server/tests -q
```

**Frontend** (from `apps/desktop`):

```bash
pnpm build        # runs `tsc --noEmit` then `vite build`
```

**Schema migrations** run automatically on `SQLiteDatabase.open()`; the current schema version is 11, including the `agent_permissions` / `agent_settings` tables introduced by the deterministic policy engine.

---

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

---

## Roadmap / Status

The following are implementation milestones on the Trajecta roadmap:

- [x] Multimodal chat backend: attachments, large-document RAG, preflight-safe SSE, LangGraph branching, model selection APIs.
- [x] Tauri + React desktop chat UI over the FastAPI/SSE contract.
- [x] Verified skill learning pipeline: trajectory capture, replay fixtures, mining, evaluation, versioning, promotion, REST APIs.
- [x] Deterministic permission engine + Settings control center (permissions, memory, skills, scheduled tasks, MCP tools).
- [ ] Guardrails AI integration in the runtime consumption path (currently deferred in favor of the deterministic policy engine).
- [ ] Production packaging (installers, code signing), multi-platform builds.
- [ ] Cloud / hosted deployment options with the local-first runtime intact.

---

## License

Licensed under the [MIT License](LICENSE). Copyright (c) 2026 Trajecta.
