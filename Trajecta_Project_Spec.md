# Trajecta

## Project Overview

**Trajecta** is a local-first autonomous desktop agent designed to learn from its own successful task trajectories.

Unlike a normal autonomous agent that simply plans, executes tools, and returns a result, Trajecta captures how tasks were solved, identifies repeated successful patterns, converts those patterns into reusable skills, evaluates those skills against previous and held-out tasks, and only promotes them when they measurably improve future performance.

The central idea is **Verified Skill Learning**:

```text
Task
  ↓
Agent Execution
  ↓
Trajectory Capture
  ↓
Pattern / Skill Mining
  ↓
Candidate Skill
  ↓
Replay + Evaluation
  ↓
Pass / Fail
  ↓
Versioned Skill Registry
  ↓
Reuse in Future Tasks
```

Trajecta will run primarily on the user's own device as a desktop application. It will support both local and cloud-hosted LLMs, local tool execution, MCP-based integrations, structured memory, configurable guardrails, sandboxed actions, and full agent observability.

The project is not intended to be a Hermes Agent clone. Its main differentiator is the lifecycle around agent learning:

- capture trajectories
- identify reusable procedures
- generate candidate skills
- test those skills
- compare them against baseline behavior
- promote only verified improvements
- version and roll back skills when needed

This turns agent experience into reusable, measurable, and controlled procedural knowledge.

---

# Tech Stack

## Desktop Application

- **Tauri 2**
  - desktop application shell
  - local-first distribution
  - native desktop packaging
  - launches the Python backend as a sidecar

- **React**
  - desktop UI

- **TypeScript**
  - frontend application logic
  - typed API contracts

---

## Agent Runtime

- **LangChain Deep Agents**
  - autonomous agent runtime
  - task planning
  - context management
  - subagents
  - long-running task execution
  - skill support
  - human-in-the-loop flows

- **LangGraph**
  - underlying execution graph
  - stateful execution
  - durable agent workflows
  - agent orchestration

---

## Backend

- **Python**
  - primary backend language

- **FastAPI**
  - communication layer between Tauri and the Python agent runtime
  - streaming agent events to the desktop UI
  - configuration and management APIs

- **Pydantic**
  - structured schemas
  - tool contracts
  - memory objects
  - skill definitions
  - provider configuration

---

## LLM Gateway

- **Bifrost**
  - unified LLM interface
  - provider abstraction
  - routing
  - fallback
  - retries
  - rate-limit handling
  - token usage tracking
  - model configuration

### Supported Model Providers

- **OpenAI**
- **Anthropic**
- **Ollama**
- **OmniRoute**
- **9Router**

Trajecta should also support a generic:

- **OpenAI-compatible endpoint adapter**

This allows additional providers and local gateways to be added without changing the main agent runtime.

---

## Local Inference

- **Ollama**
  - local model execution
  - private/offline inference where possible
  - smaller models for:
    - classification
    - routing
    - memory extraction
    - tool ranking
    - lightweight transformations

More capable hosted models can be used for:

- complex planning
- coding
- difficult reasoning
- skill generation
- evaluation

---

## Guardrails and Safety

- **Guardrails AI**
  - input validation
  - output validation
  - structured response validation
  - custom validators
  - safety checks

- **Deep Agents Permissions / Human-in-the-Loop**
  - approval before sensitive operations

- **Custom Deterministic Policy Engine**
  - tool permission enforcement
  - filesystem restrictions
  - destructive command blocking
  - action risk classification

Example action classes:

```text
SAFE
  → execute automatically

SENSITIVE
  → require user approval

DENIED
  → block execution
```

---

## Tool Runtime

Built-in tools:

- filesystem read
- filesystem write
- shell execution
- Git operations
- Python execution
- HTTP requests
- project search
- process management

External tool integration:

- **Model Context Protocol (MCP)**

Possible MCP integrations:

- GitHub
- databases
- browser tools
- filesystem tools
- documentation systems
- custom user tools

---

## Sandboxed Execution

- **Docker**
  - isolated code execution
  - restricted filesystem
  - resource limits
  - temporary workspaces
  - safer shell execution

Possible later extension:

- lightweight native sandboxing
- OS-specific permission layers

---

# Memory System

Trajecta should maintain multiple memory types instead of storing everything in one vector database.

## Working Memory

Stores temporary task state:

- current goal
- current plan
- active files
- current tool results
- execution state
- errors

## Episodic Memory

Stores previous experiences:

- previous tasks
- successful approaches
- failures
- corrections
- outcomes

## Semantic Memory

Stores durable facts:

- project structure
- user preferences
- repository information
- environment information
- discovered system facts

## Procedural Memory

Stores reusable skills:

- debugging workflows
- repository-specific procedures
- deployment procedures
- analysis strategies
- learned tool sequences

---

## Memory Storage

- **SQLite**
  - application state
  - task metadata
  - trajectory metadata
  - skill registry
  - configuration
  - episodic records

- **SQLite FTS**
  - lexical search

- **Qdrant Local** or equivalent local vector store
  - semantic retrieval
  - memory search
  - skill retrieval
  - project knowledge retrieval

---

# Skill Learning System

This is the core differentiating subsystem of Trajecta.

## Trajectory Store

Each execution stores:

```text
task
↓
agent state
↓
planning decision
↓
tool selection
↓
tool arguments
↓
tool result
↓
next state
↓
final outcome
```

---

## Skill Miner

Analyzes successful trajectories to identify:

- repeated tool sequences
- repeated reasoning structures
- repeated debugging procedures
- repeated project workflows
- reusable task patterns

Produces:

```text
Candidate Skill
```

---

## Skill Representation

Example:

```text
skills/
└── python-test-debugger/
    ├── SKILL.md
    ├── workflow.yaml
    ├── metadata.json
    ├── eval.yaml
    ├── tests/
    │   ├── case_001.yaml
    │   ├── case_002.yaml
    │   └── case_003.yaml
    └── versions/
```

---

## Skill Evaluation

Each candidate skill is compared against baseline agent behavior.

Possible evaluation dimensions:

- task success
- tool-call correctness
- number of tool calls
- token usage
- execution time
- retry count
- failure count
- guardrail violations
- human intervention count

Illustrative comparison:

```text
                 Baseline Agent      Candidate Skill

Task success          X%                   Y%
Tool calls            X                    Y
Tokens                X                    Y
Runtime               X                    Y
Failures              X                    Y
```

Actual values must come from Trajecta's own experiments.

---

## Skill Lifecycle

```text
Candidate
   ↓
Evaluate
   ↓
Pass?
 ┌─┴─┐
 │   │
No  Yes
 │   │
 ↓   ↓
Reject
     │
     ▼
 Promote
     │
     ▼
 Active Skill
     │
     ▼
 Version
     │
 ┌───┴────┐
 ▼        ▼
Upgrade  Rollback
```

---

# Observability

## OpenTelemetry

Used as the instrumentation layer for:

- agent runs
- model calls
- tool calls
- subagent execution
- memory retrieval
- skill retrieval
- guardrail checks
- evaluation
- failures
- retries

---

## Grafana Stack

- **Grafana**
  - dashboards

- **Tempo**
  - distributed traces

- **Loki**
  - logs

- **Prometheus**
  - metrics

Possible local development setup:

```text
OpenTelemetry
      ↓
Grafana LGTM Stack
      ↓
┌───────────────┬──────────────┬───────────────┐
│ Tempo         │ Loki         │ Prometheus    │
│ traces        │ logs         │ metrics       │
└───────────────┴──────────────┴───────────────┘
      ↓
   Grafana
```

---

## Agent-Level Metrics

Trajecta should expose metrics such as:

- task success rate
- model latency
- tool latency
- tool failure rate
- tokens per task
- cost per task
- subagent count
- retry count
- agent loop count
- memory retrieval latency
- skill retrieval frequency
- skill success rate
- skill regression rate
- guardrail violations
- user approvals
- local vs hosted model usage

---

# Evaluation System

Evaluation should exist at multiple levels.

## Agent Evaluation

- final task success
- correct task completion
- trajectory quality
- unnecessary loops
- failure recovery

## Tool Evaluation

- correct tool selection
- correct arguments
- tool-call success
- retry behavior

## Memory Evaluation

- relevant retrieval
- incorrect memory usage
- stale information
- memory usefulness

## Skill Evaluation

- improvement over baseline
- regression detection
- generalization
- efficiency improvement

## Model Evaluation

- task quality
- latency
- token consumption
- cost
- local vs hosted performance

---

# Full Pipeline Skeleton

```text
┌───────────────────────────────────────────────────────────────┐
│                       TRAJECTA DESKTOP                        │
│                     Tauri + React + TS                        │
│                                                               │
│  Chat | Tasks | Skills | Memory | Tools | Models | Traces    │
└──────────────────────────────┬────────────────────────────────┘
                               │
                               ▼
                    ┌──────────────────────┐
                    │    FastAPI Backend   │
                    │    Python Sidecar    │
                    └──────────┬───────────┘
                               │
                               ▼
                 ┌─────────────────────────────┐
                 │   LangChain Deep Agents     │
                 │                             │
                 │ Planner                     │
                 │ Supervisor                  │
                 │ Context Manager             │
                 │ Dynamic Subagents           │
                 │ Human-in-the-Loop           │
                 └──────────────┬──────────────┘
                                │
          ┌─────────────────────┼─────────────────────┐
          │                     │                     │
          ▼                     ▼                     ▼
┌─────────────────┐   ┌─────────────────┐   ┌─────────────────┐
│   Tool Runtime  │   │  Memory Engine  │   │   Skill Engine  │
│                 │   │                 │   │                 │
│ Filesystem      │   │ Working         │   │ Skill Search    │
│ Shell           │   │ Episodic        │   │ Skill Miner     │
│ Git             │   │ Semantic        │   │ Evaluator       │
│ Python          │   │ Procedural      │   │ Versioning      │
│ HTTP            │   │                 │   │ Promotion       │
│ MCP             │   │ SQLite          │   │ Rollback        │
└────────┬────────┘   │ FTS             │   └────────┬────────┘
         │            │ Vector Store    │            │
         │            └────────┬────────┘            │
         │                     │                     │
         └─────────────────────┼─────────────────────┘
                               │
                               ▼
                  ┌─────────────────────────┐
                  │    Guardrail Layer      │
                  │                         │
                  │ Guardrails AI           │
                  │ Permission Engine       │
                  │ Human Approval          │
                  │ Risk Classification     │
                  └────────────┬────────────┘
                               │
                               ▼
                   ┌──────────────────────┐
                   │       Bifrost        │
                   │     Model Gateway    │
                   │                      │
                   │ Routing              │
                   │ Fallback             │
                   │ Retries              │
                   │ Usage Tracking       │
                   └──────────┬───────────┘
                              │
          ┌───────────────────┼────────────────────┐
          │                   │                    │
          ▼                   ▼                    ▼
      ┌────────┐         ┌───────────┐        ┌──────────┐
      │ OpenAI │         │ Anthropic │        │  Ollama  │
      └────────┘         └───────────┘        └──────────┘
                                                   │
                           ┌───────────────────────┼─────────────┐
                           ▼                       ▼             ▼
                      OmniRoute                9Router      Other OpenAI-
                                                          Compatible APIs


────────────────────────────────────────────────────────────────

                       EXECUTION PIPELINE

User Task
   │
   ▼
Deep Agent
   │
   ▼
Retrieve Memory + Skills
   │
   ▼
Plan Task
   │
   ├─────────────► Direct Execution
   │
   └─────────────► Dynamic Subagents
                         │
                         ▼
                    Tool Requests
                         │
                         ▼
                   Guardrail Layer
                         │
                ┌────────┼────────┐
                ▼        ▼        ▼
              SAFE    SENSITIVE  DENIED
                │        │
                │        ▼
                │    User Approval
                │        │
                └────────┘
                     │
                     ▼
              Sandboxed Execution
                     │
                     ▼
                  Result
                     │
                     ▼
                Verification
                     │
                     ▼
                Final Outcome
                     │
                     ▼
              Trajectory Store
                     │
                     ▼
                 Skill Miner
                     │
                     ▼
              Candidate Skill
                     │
                     ▼
                 Evaluation
                     │
              ┌──────┴──────┐
              ▼             ▼
            FAIL           PASS
              │             │
              ▼             ▼
           Reject        Promote
                            │
                            ▼
                    Versioned Skill
                            │
                            ▼
                     Future Agent Runs


────────────────────────────────────────────────────────────────

                    OBSERVABILITY PIPELINE

Agent Runtime
    │
    ├── model calls
    ├── tool calls
    ├── memory retrieval
    ├── skill retrieval
    ├── subagents
    ├── guardrails
    ├── retries
    ├── failures
    └── evaluations
            │
            ▼
       OpenTelemetry
            │
     ┌──────┼────────┐
     ▼      ▼        ▼
   Tempo   Loki   Prometheus
     │      │        │
     └──────┼────────┘
            ▼
         Grafana
```

---

# Core Project Identity

**Trajecta is a local-first autonomous desktop agent that learns reusable skills from successful task trajectories. Candidate skills are sandboxed, replayed, evaluated, versioned, and promoted only when they measurably improve future agent performance.**

The system combines:

- Deep Agents
- structured agent memory
- MCP tools
- Guardrails AI
- deterministic permissions
- sandboxed execution
- Bifrost model routing
- OpenAI
- Anthropic
- Ollama
- OmniRoute
- 9Router
- verified skill learning
- trajectory analysis
- automated evaluation
- OpenTelemetry
- Grafana observability
- Tauri desktop delivery

The main engineering thesis is:

> **Can an autonomous agent become measurably better at repeated tasks without blindly trusting what it learns from previous executions?**


---

# Current Implementation State — 16 September 2026

This section is the durable handoff for the next Trajecta development session.

## Product Direction

Trajecta is **chat-first**, not a workflow builder. The primary product is a production-grade local autonomous desktop chat harness where the user can converse naturally, attach files, and ask the agent to perform work. Planning and subagents are dynamic capabilities used only when a request benefits from them. The long-term differentiator remains Verified Skill Learning: useful successful trajectories can later become evaluated, versioned procedural skills.

## Implemented Foundation

- Deep Agents + LangGraph memory foundation
- short-term, semantic, episodic, and procedural memory
- SQLite + FTS5 local storage
- embedded Qdrant vector layer
- Bifrost-backed Deep Agents model factory
- MCP tool discovery through LangChain MCP
- FastAPI production chat persistence
- conversations, messages, uploads, SSE streaming, cancellation
- image/PDF/DOCX/text attachment ingestion
- PDF/DOCX/text extraction companions under `/uploads/`
- Deep Agents read-only `/uploads/` filesystem route

## Chat Slice Completed in This Revision

### Large-document RAG

Large extracted documents are now chunked and indexed locally.

```text
Upload
  -> local extracted text
  -> chunking with overlap
  -> SQLite attachment_chunks
  -> FTS5 attachment_chunks_fts
  -> Qdrant attachment_chunks collection
  -> conversation-scoped search_attachments tool
  -> Deep Agent retrieves only relevant passages
```

The original uploaded file remains canonical. RAG is a retrieval optimization, not a replacement for direct visual/file inspection.

### Proper Streaming Preflight

Streaming routes now validate before returning SSE headers:

- conversation existence
- attachment ownership/existence
- non-empty message input
- active-run conflicts
- Bifrost model configuration
- MCP discovery
- LangGraph checkpoint existence for branches

Only after preflight succeeds is the SSE response created.

### LangGraph-backed Conversation Branching

Messages are immutable. Edit/resend/regenerate create a new application branch and execute from the relevant LangGraph `checkpoint_id`.

```text
prior checkpoint
      |
      +-- original user message -> original assistant
      |
      +-- edited/resend user message -> new assistant
```

Each branch tracks its own head checkpoint, and normal future messages always resume from the active branch head.

Implemented operations:

- edit a user message
- resend a user message
- regenerate an assistant answer
- list conversation branches
- activate a previous branch

### Model Selection

Implemented:

- `GET /api/v1/models` — configured models plus best-effort Bifrost discovery
- `PUT /api/v1/chat/conversations/{conversation_id}/model` — persistent model selection for a conversation
- per-turn model override remains supported in send/edit/resend/regenerate requests

### Tests Added

Tests now cover:

- immutable branch history
- branch fork behavior
- edit/regenerate flow
- invalid-attachment preflight
- large text upload + RAG indexing/search
- attachment reuse across branched messages

## Important Database Schema

Schema v3 adds the chat branching and attachment-RAG tables. Schema v4 adds an explicit `thread_id` per chat branch so a fork before the first checkpoint can start on a fresh LangGraph thread while later forks reuse the parent thread plus checkpoint.

Schema v3/v4 includes:

- `chat_branches`
- `chat_branches.thread_id`
- `branch_messages`
- `message_attachments`
- `attachment_chunks`
- `attachment_chunks_fts`
- `conversations.active_branch_id`
- `chat_messages.revision_of`
- `chat_messages.base_checkpoint_id`
- `chat_messages.checkpoint_id`

## Main Chat Endpoints

```text
POST /api/v1/chat/conversations
GET  /api/v1/chat/conversations
GET  /api/v1/chat/conversations/{id}
PUT  /api/v1/chat/conversations/{id}/model

POST /api/v1/chat/conversations/{id}/attachments
POST /api/v1/chat/conversations/{id}/messages/stream
POST /api/v1/chat/conversations/{id}/messages/{message_id}/edit/stream
POST /api/v1/chat/conversations/{id}/messages/{message_id}/resend/stream
POST /api/v1/chat/conversations/{id}/messages/{assistant_id}/regenerate/stream
POST /api/v1/chat/conversations/{id}/cancel

GET /api/v1/chat/conversations/{id}/branches
PUT /api/v1/chat/conversations/{id}/branches/{branch_id}/activate

GET /api/v1/models
```

## Known Limitations / Deliberately Deferred

1. **Guardrails AI is intentionally not wired into chat yet.** It remains a later layer.
2. **The current VectorStore embedder is still the deterministic local placeholder.** FTS5 is real lexical retrieval, but production semantic quality requires replacing the hash embedding with a real local or Bifrost-backed embedding model.
3. **Scanned/image-only PDFs do not have OCR.** Native multimodal file reading may still work with a capable model, but local text extraction will be empty.
4. **Bifrost model discovery uses `/v1/models` as a best-effort OpenAI-compatible endpoint.** Configured/default models are always returned even if the gateway does not expose that endpoint.
5. **Desktop UI is not implemented yet.** The backend API is now ready for it.
6. **Trajectory capture / OpenTelemetry / Grafana / Skill Miner are not part of this slice.** They come after the production chat experience.

## Recommended Next Slice

Build the **Tauri + React desktop chat UI** against the backend contract above:

- conversation sidebar
- streaming assistant messages
- drag/drop multi-file upload and previews
- tool/subagent progress UI
- stop/cancel
- edit/resend/regenerate controls
- branch/variant navigation
- model selector
- attachment chips + document retrieval state
- robust error/reconnect handling

After the desktop chat loop is usable end-to-end, add OpenTelemetry trajectory capture so agent learning is built on real user interactions rather than synthetic workflows.
