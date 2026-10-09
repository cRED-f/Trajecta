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

```mermaid
%%{init: {"flowchart": {"nodeSpacing": 16, "rankSpacing": 24, "padding": 6}, "themeVariables": {"fontSize": "12px"}}}%%
flowchart TB
    %% Compact groups retain the full architecture without one box per feature.
    subgraph DESKTOP["01 · DESKTOP EXPERIENCE"]
        UI["User"]
    end

    subgraph BACKEND["02 · FASTAPI APPLICATION"]
        API["REST /api/v1 · chat / branches / regeneration<br/>attachments: extract / chunk / index · scheduler<br/>provider / MCP / policy / review APIs · SSE events"]
    end

    subgraph PREFLIGHT["03 · INPUT GUARDRAILS & PREFLIGHT"]
        direction LR
        INPUT["Input guardrails<br/>Prompt injection · optional jailbreak"]
        DECIDE{"Configured<br/>action?"}
        BLOCK["Block request"]
        PREP["Pass / warn → preflight<br/>Model · folder · tools · checkpoint"]
        INPUT --> DECIDE
        DECIDE -->|Block| BLOCK
        DECIDE -->|Pass / warn| PREP
    end

    subgraph ENGINE["04 · STATEFUL AGENT"]
        AGENT["Deep Agents + LangGraph<br/>Planning / subagents · edit / resend / regenerate<br/>Checkpoints · human questions · HITL resume · SSE output"]
    end

    subgraph EXECUTION["05–07 · MODEL & TOOL EXECUTION"]
        direction LR
        subgraph MODEL_LANE["MODEL ROUTE"]
            direction TB
            MODEL_G["Model-context guardrails<br/>Untrusted content · secrets · PII"]
            BIFROST["Bifrost gateway<br/>OpenAI · Anthropic · 9Router<br/>compatible APIs · Ollama + embeddings"]
            MODEL_G --> BIFROST
        end
        subgraph TOOL_LANE["TOOL ROUTE"]
            direction TB
            POLICY{"ALLOW / ASK / DENY"}
            APPROVAL["ASK → human approval<br/>durable interrupt / resume"]
            TOOLSET["Enabled local + MCP tools<br/>validated workspace · optional Docker<br/>connector read-back verification"]
            DENY["DENY → no execution"]
            POLICY -->|ALLOW| TOOLSET
            POLICY -->|ASK| APPROVAL
            APPROVAL -->|Approved| TOOLSET
            POLICY -->|DENY| DENY
        end
    end

    subgraph KNOWLEDGE["08 · KNOWLEDGE, SEARCH & RAG"]
        RETRIEVE["Hybrid retrieval · scope / rank / conflicts<br/>Semantic facts · episodic evidence · procedural records<br/>active versioned skills · attachment RAG · manual curator"]
    end

    subgraph LEARNING["09 · EXPERIENCE-DRIVEN LEARNING"]
        direction TB
        EVENTS["Append-only trajectories · tool events · metrics<br/>Completed ≠ independently verified success"]
        LEARN["Explicit preferences → learned context<br/>corrections · durable, budgeted reflection queue<br/>Bifrost insights → evidence-linked procedure drafts"]
        REVIEW["Human review · approve / reject / archive"]
        SKILLS["Explicit candidate → manual replay / regression eval<br/>versioned skill activation / rollback"]
        EVENTS --> LEARN --> REVIEW --> SKILLS
    end

    subgraph STORAGE["10 · DURABLE LOCAL DATA"]
        STORE["SQLite · FTS5 · embedded Qdrant<br/>Chats · settings · jobs · audits · checkpoints<br/>attachment files / workspace documents"]
    end

    %% Primary request path; scheduled prompts use the same chat preflight.
    UI --> API --> INPUT
    PREP --> RETRIEVE --> AGENT
    AGENT -->|Model call| MODEL_G
    AGENT -->|Tool action| POLICY
    AGENT -. "Events / feedback" .-> EVENTS
    SKILLS -. "Approved versions" .-> RETRIEVE
    LEARN -. "Explicit preferences" .-> RETRIEVE
    RETRIEVE --> STORE
    EVENTS --> STORE
    API -. "Persistent records" .-> STORE

    %% Amber highlights trust boundaries; other hues indicate responsibility.
    classDef ui fill:#172033,stroke:#94A3B8,color:#F8FAFC,stroke-width:1.3px;
    classDef api fill:#1F2937,stroke:#93C5FD,color:#F8FAFC,stroke-width:1.3px;
    classDef critical fill:#4A2D12,stroke:#FBBF24,color:#FFFBEB,stroke-width:1.7px;
    classDef agent fill:#312E81,stroke:#A5B4FC,color:#FFFFFF,stroke-width:1.4px;
    classDef model fill:#164E63,stroke:#67E8F9,color:#ECFEFF,stroke-width:1.3px;
    classDef tool fill:#1E3A5F,stroke:#60A5FA,color:#EFF6FF,stroke-width:1.3px;
    classDef knowledge fill:#134E4A,stroke:#5EEAD4,color:#F0FDFA,stroke-width:1.3px;
    classDef learn fill:#3B3055,stroke:#D8B4FE,color:#FAF5FF,stroke-width:1.3px;
    classDef storage fill:#193927,stroke:#86EFAC,color:#F0FDF4,stroke-width:1.3px;
    classDef denied fill:#522222,stroke:#FCA5A5,color:#FEF2F2,stroke-width:1.3px;
    class UI ui;
    class API api;
    class INPUT,DECIDE,PREP,MODEL_G,POLICY,APPROVAL critical;
    class BLOCK,DENY denied;
    class AGENT agent;
    class BIFROST model;
    class TOOLSET tool;
    class RETRIEVE knowledge;
    class EVENTS,LEARN,REVIEW,SKILLS learn;
    class STORE storage;
```

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
- **Infrastructure:** Docker Compose, Guardrails; optional sandbox services.

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
