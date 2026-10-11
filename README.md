<p align="center">
  <img src="apps/desktop/src-tauri/icons/icon.png" alt="Trajecta app icon" width="112" />
</p>

<h1 align="center">Trajecta</h1>

<p align="center"><strong>A desktop AI agent that learns from experience.</strong></p>

<p align="center">Chat · Tools · Knowledge · Background learning · Human control</p>

---

> **Status:** Active development.

## Overview

Trajecta is a Tauri 2 desktop application with a Python/FastAPI agent backend. It can execute multi-step tasks, search stored knowledge, and learn from prior interactions while keeping sensitive tool actions behind explicit permissions.

- **Act:** LangChain Deep Agents, local tools, MCP integrations, Docker-free local command execution, and user-selected models.
- **Remember:** Saved facts, prior task experiences, document context, and reusable skills.
- **Learn:** A durable background worker links related chat runs into logical tasks and generates evidence-backed insights and skill candidates.
- **Protect:** Guardrails AI checks prompt injection, untrusted tool content, and sensitive context before cloud-model calls.
- **Control:** `ALLOW / ASK / DENY` permissions remain in force even when knowledge is created automatically.

## Features

- **Desktop chat:** SSE streaming, expandable reasoning/tool activity, branching and regeneration, message editing, and stop/cancellation handling.
- **Tools & workspaces:** Local files, web and document tools, MCP integrations, and per-conversation folder selection.
- **Docker-free local commands:** Deep Agents `execute` uses the installed Windows PowerShell and other tools via Python subprocess, with terminal approval and timeouts. **Commands are not sandboxed:** they can access host files and network resources as the current user.
- **Knowledge Center:** Semantic memory (facts/preferences), episodic memory (task history), and procedural memory (versioned skills).
- **Autonomous learning:** Multi-turn task tracking, background reflection, procedural candidate synthesis, and evidence-aware consolidation without routine review prompts.
- **Skill quality controls:** Independent replay/held-out evaluation and safety checks gate activation; unverified candidates remain inactive.
- **Guardrails AI:** Configurable prompt-injection detection, protection from instructions in untrusted tool results, cloud-bound secret/PII redaction, and structured-output validation.
- **Models:** Bifrost gateway with configured cloud/OpenAI-compatible providers; optional Ollama and 9Router connections.
- **Automation:** One-time, interval, and cron scheduled tasks.
- **Desktop lifecycle:** One global `trajecta` launcher, system tray, optional login startup, managed FastAPI, and in-app Git-based updates/diagnostics.

## Architecture

```mermaid
flowchart TB
    subgraph DESKTOP["01 · TAURI DESKTOP"]
        direction LR
        UI["React UI<br/>Chat · Knowledge · Settings"]
        HOST["Native desktop manager<br/>Tray · Startup · FastAPI supervision"]
    end

    subgraph CHAT["02 · FASTAPI · REAL-TIME CHAT"]
        direction TB
        API["Chat API"]
        INPUT["Guardrails AI<br/>User prompt inspection"]
        RET["Unified knowledge retrieval<br/>Facts · Episodes · Active skills"]
        AGENT["LangChain Deep Agent"]
        CONTEXT["Guardrails AI · Model boundary<br/>Untrusted tool-text checks<br/>Cloud-bound secrets / PII protection"]
        MODEL["Bifrost gateway<br/>Configured LLM"]
        POLICY{"Tool policy<br/>ALLOW / ASK / DENY"}
        TOOLS["Local · Web · Files · MCP tools"]
        EXEC["Local subprocess executor<br/>PowerShell · Python · Git · npm<br/>Host user permissions · Timeout"]
        STREAM["SSE response stream"]

        API --> INPUT --> RET --> AGENT
        AGENT --> CONTEXT --> MODEL --> AGENT
        AGENT --> POLICY
        POLICY -->|Approved shell / Python| EXEC
        POLICY -->|Other authorized actions| TOOLS
        EXEC -->|Execution result| AGENT
        TOOLS -->|Result| AGENT
        AGENT --> STREAM
    end

    UI --> API
    HOST -.->|Start / health / recovery| API
    STREAM --> UI

    subgraph LEARNING["03 · NON-BLOCKING AUTONOMOUS LEARNING"]
        direction TB
        EVENTS["Durable run & tool events"]
        TASK["Logical task tracking<br/>Multi-turn · Cross-session"]
        JOBS["Background learning queue"]
        REFLECT["Bifrost reflection<br/>Privacy-protected context"]
        SEM["Semantic memory<br/>Facts · Preferences"]
        EP["Episodic memory<br/>Task history · Outcomes"]
        CAND["Procedural candidates<br/>Reusable workflows"]
        EVAL["Replay / held-out evaluation<br/>Guardrails AI structured-output checks"]
        ACTIVE["Verified, versioned active skills"]
        PENDING["Inactive candidates<br/>Insufficient evidence"]

        EVENTS --> TASK --> JOBS --> REFLECT
        REFLECT --> SEM
        REFLECT --> EP
        REFLECT --> CAND --> EVAL
        EVAL -->|Passes gates| ACTIVE
        EVAL -->|Not yet verified| PENDING
    end

    API -.-> EVENTS
    TOOLS -.-> EVENTS
    EXEC -.-> EVENTS

    subgraph STORAGE["04 · PERSISTENT KNOWLEDGE"]
        direction LR
        SQL[("SQLite · FTS5<br/>Tasks · Events · Memories · Skill versions")]
        VECTOR[("Qdrant<br/>Vector index")]
    end

    SEM --> SQL
    EP --> SQL
    ACTIVE --> SQL
    SQL -.-> RET
    SQL -.-> VECTOR
    VECTOR -.-> RET
```

The chat response **does not wait for task classification, reflection, or skill evaluation**. Individual runs retain their event history, while logical tasks can continue across turns or sessions. Task association currently uses conservative heuristics rather than perfect semantic understanding. Inactivity can checkpoint a task without marking it successful.

## Install from a Git clone (Windows)

### Prerequisites

- Windows 10/11 and Microsoft Edge WebView2.
- Git, Node.js 22+, pnpm, and [uv](https://docs.astral.sh/uv/).
- Rust toolchain with the MSVC target and Visual Studio C++ build tools (for Tauri); see [Tauri 2 prerequisites](https://v2.tauri.app/start/prerequisites/).
- Internet access to download dependencies on first installation. `uv` provisions Python 3.11 for the managed backend.
- A reachable **Bifrost** gateway and configured model credentials for AI chat. It is **not bundled/downloaded automatically** by the source installer.

### First installation

Run these in PowerShell (replace the repository URL with your actual Git remote):

```powershell
git clone https://github.com/cRED-f/Trajecta.git
cd Trajecta
pnpm trajecta:install
trajecta
```

### Persistent data and updates

The installed application uses an isolated writable directory rather than storing live data inside the source checkout:

```text
%LOCALAPPDATA%\ai.trajecta.desktop\
├── runtime\Trajecta.exe         # Locally compiled desktop executable
├── runtime\venv\                # Managed Python backend
├── data\.trajecta\              # Conversations, SQLite, Qdrant, skills and uploads
├── logs\backend.log             # FastAPI startup/runtime diagnostics
├── desktop.json                 # Desktop preferences
└── install.json                 # Git checkout reference
```

**Settings → Local Execution** lets you enable/disable command execution and configure a timeout. Commands run directly on the host with your user permissions: the selected folder is a working directory, **not** a filesystem or network restriction. Keep **Permissions → Run commands** on ASK.

**Settings → Desktop & Application → Check for updates** inspects the configured Git branch; **Update & restart** pulls and rebuilds from the clone. Commit or stash local changes first. The updater keeps previous runtime artifacts as rollback backups and retains production data, but schema migrations and data backups still require care.

Development and installed Trajecta should **never share live SQLite/Qdrant files**. Historical `.trajecta` data is not silently migrated from an old checkout. Back it up consistently with the old backend stopped before moving it. See [SOURCE_INSTALL.md](SOURCE_INSTALL.md) for details, limitations, and uninstall behavior.

## Repository layout

```text
Trajecta/
├── apps/desktop/            # React / Tauri desktop app and settings
├── cli/                     # Single global trajecta launcher
├── scripts/                 # Local install, update, and uninstall helpers
├── server/src/
│   ├── api/                 # FastAPI endpoints
│   ├── chat/                # Conversation and streaming orchestration
│   ├── guardrails/          # Guardrails AI content/privacy checks and tool policy
│   ├── memory/              # Task tracking, reflection, retrieval and storage
│   ├── skills/              # Candidates, evaluation, promotion and rollback
│   ├── tools/               # Local tools and MCP integrations
│   └── llm_gateway/         # Bifrost integration
├── config/                  # Defaults and guardrail rules
├── pyproject.toml
└── SOURCE_INSTALL.md
```

## Further documentation

- [Source installation and recovery](SOURCE_INSTALL.md)
- [Desktop](apps/desktop/REQUIREMENTS.md) · [Backend](server/REQUIREMENTS.md)
- [Memory](server/src/memory/REQUIREMENTS.md) · [Skills](server/src/skills/REQUIREMENTS.md)
- [Guardrails](server/src/guardrails/REQUIREMENTS.md) · [Tools](server/src/tools/REQUIREMENTS.md)
- [LLM gateway](server/src/llm_gateway/REQUIREMENTS.md) · [Observability](server/src/observability/REQUIREMENTS.md)

## License

[MIT](LICENSE)
