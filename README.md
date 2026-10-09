<p align="center">
  <img src="apps/desktop/src-tauri/icons/icon.png" alt="Trajecta app icon" width="112" />
</p>

<h1 align="center">Trajecta</h1>

<p align="center"><strong>A desktop AI agent that learns from experience.</strong></p>

<p align="center">
  Chat · Tools · Knowledge · Automation · Human control
</p>

---

## Overview

**Trajecta** combines a desktop workspace, agentic execution, and persistent learning.

- **Act:** Run multi-step tasks using local tools, MCP integrations, and selected AI models.
- **Remember:** Retrieve relevant preferences, facts, previous tasks, and documents.
- **Improve:** Capture corrections and propose reusable procedures from real experience.
- **Stay in control:** Approve sensitive actions and skill changes before they take effect.

> **Status:** Active development.

## Features

- **Desktop chat:** Streaming, conversation history, edit/resend/regenerate, branching, cancellation.
- **Agent runtime:** LangChain Deep Agents, LangGraph checkpoints, subagents, resumable approvals.
- **Knowledge Center:** Overview, Memories, Skills, and Review in one place.
- **Learning:** Preferences, corrections, evidence-linked procedures, bounded reflection.
- **Memory & RAG:** Semantic, episodic, procedural, attachment search, hybrid retrieval.
- **Local workspaces:** Select and validate a folder for each conversation.
- **Models:** Bifrost routing for OpenAI, Anthropic, Ollama, 9Router, and compatible APIs.
- **Tools:** Web, documents, files, local utilities, MCP, optional Docker sandbox.
- **Safety:** Context guardrails, `allow / ask / deny`, human questions and approvals.
- **Scheduling:** One-time, recurring, and cron tasks that retain agent permissions.

## Architecture

**End-to-end system map** — request processing, agent execution, model routing, tool authority, long-term knowledge, and experience-driven skill improvement.

```mermaid
flowchart TB
    %% ───────────────────────────────────────────────────────────────
    %% 01. Product surface
    %% ───────────────────────────────────────────────────────────────
    subgraph DESKTOP["01 · DESKTOP EXPERIENCE"]
        direction LR
        SHELL["Tauri 2 shell<br/>React 19 · TypeScript"]
        CHATUI["Chat workspace<br/>SSE · activity timeline · branching"]
        KNOWUI["Knowledge Center<br/>Memories · Skills · Review"]
        TASKUI["Scheduled Tasks<br/>One-time · interval · cron"]
        SETTINGSUI["Settings<br/>Models · tools · permissions"]
    end

    %% ───────────────────────────────────────────────────────────────
    %% 02. HTTP and application services
    %% ───────────────────────────────────────────────────────────────
    subgraph BACKEND["02 · FASTAPI APPLICATION LAYER"]
        direction LR
        REST["REST API · /api/v1"]
        CHAT_SVC["Chat service<br/>Conversations · edits · regeneration"]
        FILES["Attachment ingestion<br/>Extract · chunk · index"]
        SCHED["Durable task scheduler<br/>Run through chat service"]
        CONTROL["Configuration APIs<br/>Providers · MCP · policies"]
        LEARN_API["Knowledge & review APIs"]
        SSE["SSE stream<br/>Tokens · tools · interrupts · outcomes"]
    end

    %% ───────────────────────────────────────────────────────────────
    %% 03. Input trust boundary
    %% ───────────────────────────────────────────────────────────────
    subgraph PREFLIGHT["03 · REQUEST VALIDATION & INPUT GUARDRAILS"]
        direction LR
        INPUT_G["Input guardrails<br/>Prompt-injection check · optional jailbreak"]
        INPUT_DECIDE{"Configured action?"}
        STOP["Block request<br/>if policy requires"]
        PREP["Run preflight<br/>Model · workspace · tools · checkpoint"]
        CONTEXT_INIT["Build bounded context<br/>Relevant memory · active skills"]
    end

    %% ───────────────────────────────────────────────────────────────
    %% 04. Stateful agent engine
    %% ───────────────────────────────────────────────────────────────
    subgraph ENGINE["04 · STATEFUL AGENT EXECUTION"]
        direction LR
        GRAPH["LangChain Deep Agents<br/>LangGraph orchestration"]
        PLAN["Task planning<br/>Subagent delegation"]
        THREADS["Conversation branches<br/>Edit · resend · regenerate"]
        CHECKPOINT["LangGraph checkpoints<br/>Interrupt & resume"]
        ASK_USER["Human questions<br/>Ask for missing information"]
        OUTPUT["Streaming response<br/>Text · tool events · status"]
    end

    %% ───────────────────────────────────────────────────────────────
    %% 05. Safety for LLM and tool actions
    %% ───────────────────────────────────────────────────────────────
    subgraph SECURITY["05 · SECURITY & HUMAN CONTROL"]
        direction LR
        MODEL_G["Model-context guardrails<br/>Untrusted content · secrets · PII"]
        POLICY["Deterministic permission policy"]
        CHOICE{"ALLOW / ASK / DENY"}
        USER_OK["Human approval<br/>Durable HITL interrupt"]
        NO_EXEC["Deny tool action"]
        RECEIPT["Connector verification<br/>Read-back receipts where supported"]
    end

    %% ───────────────────────────────────────────────────────────────
    %% 06. Model infrastructure
    %% ───────────────────────────────────────────────────────────────
    subgraph MODELS["06 · MODEL & INFERENCE PLANE"]
        direction LR
        BIFROST["Bifrost gateway<br/>Model selection · provider routing"]
        CLOUD["OpenAI · Anthropic"]
        ROUTER["9Router · compatible APIs"]
        OLLAMA["Ollama<br/>Local generation · embeddings"]
    end

    %% ───────────────────────────────────────────────────────────────
    %% 07. Extensible tools
    %% ───────────────────────────────────────────────────────────────
    subgraph ACTIONS["07 · TOOLS & WORKSPACE EXECUTION"]
        direction LR
        TOOL_REG["Tool registry<br/>Enabled capabilities"]
        PERSONAL["Personal tools<br/>Web · documents · browser · system"]
        MCP["MCP tool gateway<br/>Enabled servers & tools"]
        WORKSPACE["Selected local workspace<br/>Validated folder mapping"]
        SANDBOX["Optional Docker sandbox<br/>Isolated execution paths"]
    end

    %% ───────────────────────────────────────────────────────────────
    %% 08. Retrieval and knowledge
    %% ───────────────────────────────────────────────────────────────
    subgraph KNOWLEDGE["08 · KNOWLEDGE, SEARCH & RAG"]
        direction LR
        RETRIEVE["Unified hybrid retriever<br/>Relevance · scope · conflict checks"]
        SEM["Semantic memory<br/>Facts · preferences"]
        EPI["Episodic memory<br/>Past runs · verified status"]
        PROC["Procedural records<br/>Observed workflows"]
        SKILL["Active version-matched skills"]
        ATTACH["Attachment RAG<br/>Conversation-scoped search"]
        CURATOR["Manual curator<br/>Maintenance findings"]
    end

    %% ───────────────────────────────────────────────────────────────
    %% 09. Experience-led learning
    %% ───────────────────────────────────────────────────────────────
    subgraph LEARNING["09 · EXPERIENCE-DRIVEN LEARNING LIFECYCLE"]
        direction LR
        TRAJ["Append-only trajectories<br/>Messages · tool events · outcomes"]
        METRICS["Metrics & feedback<br/>Completion is not verified success"]
        EXPERIENCE["Learned experiences<br/>Explicit preferences · corrections"]
        QUEUE["Durable reflection queue<br/>Rate limits · restart recovery"]
        REFLECT["Bounded LLM reflection<br/>Evidence-linked insights"]
        PROPOSAL["Procedure suggestions<br/>Reviewable drafts & revisions"]
        REVIEW["Human knowledge review<br/>Approve · reject · archive"]
        CANDIDATE["Explicit skill candidate"]
        EVALUATE["Manual evaluation<br/>Replay · regression checks"]
        PROMOTE["Versioned activation<br/>Promotion · rollback"]
    end

    %% ───────────────────────────────────────────────────────────────
    %% 10. Storage and durability
    %% ───────────────────────────────────────────────────────────────
    subgraph STORAGE["10 · DURABLE LOCAL DATA"]
        direction LR
        SQLITE["SQLite<br/>Chats · settings · jobs · learning · audits"]
        FTS["SQLite FTS5<br/>Exact & lexical retrieval"]
        QDRANT["Embedded Qdrant<br/>Semantic vector search"]
        PERSIST["LangGraph checkpoint store"]
        ASSETS["Local attachment files<br/>Workspace documents"]
    end

    %% ───────────────────────────────────────────────────────────────
    %% Primary user and automation paths
    %% ───────────────────────────────────────────────────────────────
    SHELL --- CHATUI
    SHELL --- KNOWUI
    SHELL --- TASKUI
    SHELL --- SETTINGSUI
    CHATUI --> REST
    KNOWUI --> LEARN_API
    TASKUI --> REST
    SETTINGSUI --> CONTROL
    REST --> CHAT_SVC
    REST --> FILES
    REST --> SCHED
    SCHED -->|"Scheduled prompt"| CHAT_SVC
    CHAT_SVC --> INPUT_G --> INPUT_DECIDE
    INPUT_DECIDE -->|"Block, if configured"| STOP
    INPUT_DECIDE -->|"Pass / warn"| PREP
    PREP --> CONTEXT_INIT --> GRAPH
    GRAPH --> OUTPUT --> SSE --> CHATUI

    %% Stateful orchestration
    CHAT_SVC <--> THREADS
    GRAPH <--> CHECKPOINT
    THREADS --> CHECKPOINT
    GRAPH --> PLAN
    GRAPH -->|"Clarification"| ASK_USER --> SSE

    %% Model requests cross a separate inspection boundary
    GRAPH -->|"Model request"| MODEL_G --> BIFROST
    BIFROST --> CLOUD
    BIFROST --> ROUTER
    BIFROST --> OLLAMA
    BIFROST -. "Model responses" .-> GRAPH
    CONTROL -. "Provider settings" .-> BIFROST

    %% Tools require independent authorization
    GRAPH -->|"Tool request"| POLICY --> CHOICE
    CHOICE -->|"ALLOW"| TOOL_REG
    CHOICE -->|"ASK"| USER_OK
    CHOICE -->|"DENY"| NO_EXEC
    USER_OK -->|"Approved / resumed"| TOOL_REG
    USER_OK -. "Interrupt event" .-> SSE
    TOOL_REG --> PERSONAL
    TOOL_REG --> MCP
    TOOL_REG -. "Configured execution" .-> SANDBOX
    PERSONAL -->|"Workspace-scoped tools"| WORKSPACE
    MCP -. "Supported mutating actions" .-> RECEIPT
    PERSONAL -. "Tool result" .-> GRAPH
    MCP -. "Tool result" .-> GRAPH
    WORKSPACE -. "Files & context" .-> GRAPH
    PREP -->|"Validate selected folder"| WORKSPACE

    %% Memory and document retrieval
    CONTEXT_INIT --> RETRIEVE
    RETRIEVE --> SEM
    RETRIEVE --> EPI
    RETRIEVE --> SKILL
    RETRIEVE --> ATTACH
    FILES --> ATTACH
    FILES --> ASSETS
    SEM --> SQLITE
    EPI --> SQLITE
    PROC --> SQLITE
    SKILL --> SQLITE
    RETRIEVE --> FTS
    RETRIEVE --> QDRANT
    ATTACH --> FTS
    ATTACH --> QDRANT
    CURATOR -. "Manual inspection" .-> SQLITE
    OLLAMA -. "Selected embedding model" .-> QDRANT

    %% Feedback, background review and manual skill promotion
    GRAPH -. "Run events" .-> TRAJ
    OUTPUT -. "Completion record" .-> METRICS
    LEARN_API --> REVIEW
    LEARN_API --> METRICS
    TRAJ --> METRICS
    METRICS --> EXPERIENCE
    TRAJ --> QUEUE --> REFLECT
    REFLECT -. "Review model through Bifrost" .-> BIFROST
    REFLECT --> PROPOSAL
    REFLECT -. "Lessons / corrections to review" .-> REVIEW
    EXPERIENCE -. "Corrections needing review" .-> REVIEW
    PROPOSAL --> REVIEW
    REVIEW -->|"Approved draft + explicit creation"| CANDIDATE
    CANDIDATE -. "Explicit / manual" .-> EVALUATE
    EVALUATE -->|"Promotion gate"| PROMOTE
    PROMOTE --> SKILL
    EXPERIENCE -. "Active approved context" .-> RETRIEVE
    TRAJ -. "Episode summaries when enabled" .-> EPI
    TRAJ --> SQLITE
    METRICS --> SQLITE
    QUEUE --> SQLITE
    PROPOSAL --> SQLITE
    REVIEW --> SQLITE
    SCHED --> SQLITE
    CONTROL --> SQLITE
    CHECKPOINT --> PERSIST --> SQLITE

    %% Styling: amber = boundaries/control, violet = agent, teal = retrieval.
    classDef ui fill:#172033,stroke:#94A3B8,color:#F8FAFC,stroke-width:1.3px;
    classDef api fill:#1F2937,stroke:#93C5FD,color:#F8FAFC,stroke-width:1.3px;
    classDef critical fill:#4A2D12,stroke:#FBBF24,color:#FFFBEB,stroke-width:2px;
    classDef agent fill:#312E81,stroke:#A5B4FC,color:#FFFFFF,stroke-width:1.5px;
    classDef model fill:#164E63,stroke:#67E8F9,color:#ECFEFF,stroke-width:1.3px;
    classDef tool fill:#1E3A5F,stroke:#60A5FA,color:#EFF6FF,stroke-width:1.3px;
    classDef knowledge fill:#134E4A,stroke:#5EEAD4,color:#F0FDFA,stroke-width:1.3px;
    classDef learn fill:#3B3055,stroke:#D8B4FE,color:#FAF5FF,stroke-width:1.3px;
    classDef storage fill:#193927,stroke:#86EFAC,color:#F0FDF4,stroke-width:1.3px;
    classDef denied fill:#522222,stroke:#FCA5A5,color:#FEF2F2,stroke-width:1.3px;
    class SHELL,CHATUI,KNOWUI,TASKUI,SETTINGSUI ui;
    class REST,CHAT_SVC,FILES,SCHED,CONTROL,LEARN_API,SSE api;
    class INPUT_G,INPUT_DECIDE,PREP,MODEL_G,POLICY,CHOICE,USER_OK critical;
    class STOP,NO_EXEC denied;
    class CONTEXT_INIT,GRAPH,PLAN,THREADS,CHECKPOINT,ASK_USER,OUTPUT agent;
    class BIFROST,CLOUD,ROUTER,OLLAMA model;
    class TOOL_REG,PERSONAL,MCP,WORKSPACE,SANDBOX,RECEIPT tool;
    class RETRIEVE,SEM,EPI,PROC,SKILL,ATTACH,CURATOR knowledge;
    class TRAJ,METRICS,EXPERIENCE,QUEUE,REFLECT,PROPOSAL,REVIEW,CANDIDATE,EVALUATE,PROMOTE learn;
    class SQLITE,FTS,QDRANT,PERSIST,ASSETS storage;
```

**Legend:** Solid arrows = primary runtime or data flow · Dashed arrows = conditional, optional, or manual relationships · Amber = security and human-control boundaries.

> **Accuracy notes:** Input checks warn by default unless blocking is configured. Model-context protection applies before model calls; tool permissions are enforced separately. Reflection never activates a skill automatically; evaluation and promotion are explicit operations. Docker sandboxing is optional, and full OpenTelemetry/Grafana integration is not represented as a completed feature.

## How learning works

1. **Capture** — conversation, tool activity, outcome evidence, and feedback.
2. **Extract** — explicit preferences, corrections, and procedure suggestions.
3. **Review** — uncertain lessons and procedural changes require human decisions.
4. **Promote** — an executable skill requires an explicit candidate and manual evaluation/activation.
5. **Recall** — relevant approved knowledge and active skills can inform future work.

**Important distinctions**

- **Completed ≠ successful:** outcomes stay unverified until supported by feedback or other evidence.
- **Remembered ≠ approved:** pending or rejected suggestions are not active skills.
- **Evaluation is manual:** routine chats do not automatically benchmark every skill candidate.
- **Model reasoning is conditional:** the UI shows reasoning only when the provider exposes it.

## Technology

- **Desktop:** Tauri 2, React 19, TypeScript, Vite, Lucide icons.
- **Backend:** Python 3.11+, FastAPI, LangChain Deep Agents, LangGraph.
- **Models:** Bifrost gateway; cloud, Ollama, and OpenAI-compatible providers.
- **Search & storage:** SQLite, FTS5, embedded Qdrant; configurable Ollama embeddings.
- **Infrastructure:** Docker Compose; optional sandbox services.

## Quick start

### Prerequisites

- Python **3.11+** and [uv](https://docs.astral.sh/uv/)
- Node.js, pnpm, Rust, and [Tauri prerequisites](https://v2.tauri.app/start/prerequisites/)
- Docker + Docker Compose for Bifrost
- Optional: [Ollama](https://ollama.com/) for local models and embeddings

### 1. Configure

```bash
cp .env.example .env
```

> Windows PowerShell: `Copy-Item .env.example .env`. Match `BIFROST_VIRTUAL_KEY` to the key configured in Bifrost; do not commit secrets.

### 2. Start the gateway

```bash
docker compose up -d bifrost
```

### 3. Start the backend

```bash
uv sync
uv run python -m server.src.main
```

### 4. Start the desktop app

```bash
cd apps/desktop
pnpm install
pnpm tauri dev
```

- **Backend:** `http://127.0.0.1:8420/api/v1`
- **Bifrost:** `http://127.0.0.1:8080`
- **Browser-only UI:** `pnpm dev` (native folder selection needs Tauri)
- **First launch:** Set up **Settings → LLM Providers**; optionally choose an Ollama embedding model.

## Development

```bash
# Repository root — backend tests
uv run python -m pytest server/tests -q

# apps/desktop — frontend tests and build
pnpm test
pnpm build

# apps/desktop — native desktop build
pnpm tauri build
```

> Commands reflect the repository setup; passing all tests and desktop packaging across platforms is not yet guaranteed.

## Repository layout

```text
Trajecta/
├── apps/desktop/          # React interface and Tauri shell
├── server/
│   ├── src/
│   │   ├── api/          # FastAPI routes
│   │   ├── chat/         # Streaming, conversations, branches, RAG
│   │   ├── agent/        # Agent orchestration and HITL
│   │   ├── memory/       # Memory, retrieval, embeddings
│   │   ├── skills/       # Learning, evaluation, versioning
│   │   ├── tools/        # Local utilities, MCP, sandbox
│   │   ├── guardrails/   # Content checks and permissions
│   │   └── llm_gateway/  # Bifrost integration
│   └── tests/            # Backend tests
├── config/               # Defaults, Bifrost, guardrail rules
├── infra-docker/         # Supporting services and sandbox
├── docker-compose.yaml
└── pyproject.toml
```

## Documentation

- [Desktop](apps/desktop/REQUIREMENTS.md) · [Backend](server/REQUIREMENTS.md)
- [Memory](server/src/memory/REQUIREMENTS.md) · [Skills](server/src/skills/REQUIREMENTS.md)
- [Guardrails](server/src/guardrails/REQUIREMENTS.md) · [Tools](server/src/tools/REQUIREMENTS.md)
- [LLM gateway](server/src/llm_gateway/REQUIREMENTS.md) · [Observability](server/src/observability/REQUIREMENTS.md)
- [Infrastructure](infra-docker/REQUIREMENTS.md)

## License

[MIT](LICENSE)
