# Desktop Application Requirements

## Overview

Tauri 2 desktop application providing the user-facing interface for Trajecta. React + TypeScript frontend launched as a native desktop window; the Python server runs as a Tauri sidecar process.

## UI Views

| View | Purpose |
|------|---------|
| Chat | Conversational interface to the agent |
| Tasks | Active and completed task history |
| Skills | Skill registry, versions, promotion status |
| Memory | Browse working/episodic/semantic/procedural memory |
| Tools | Configured built-in and MCP tools |
| Models | LLM provider configuration and status |
| Traces | OpenTelemetry trace viewer (embedded Grafana or raw) |

## Technical Requirements

- **Tauri 2** for native desktop shell and sidecar management
- **React 18+** with functional components and hooks
- **TypeScript** strict mode
- **Vite** as the build tool
- Streaming event display from FastAPI SSE/WebSocket backend
- Local state management via Zustand or equivalent lightweight store
- Typed API contracts via shared types package

## File Map

```
apps/desktop/
├── src/
│   ├── components/       # View components per UI section
│   ├── hooks/            # Custom React hooks (useAgent, useStream, etc.)
│   ├── lib/              # API client, helpers
│   ├── stores/           # Client-side state stores
│   ├── types/            # TypeScript type definitions
│   ├── main.tsx          # App entry point
│   └── App.tsx           # Root component + routing
├── src-tauri/
│   └── src/              # Tauri Rust side (sidecar config, commands)
├── public/               # Static assets
├── package.json
├── tsconfig.json
└── vite.config.ts
```

## Key Concerns

- The desktop app communicates with the Python server over HTTP + SSE (streaming agent events)
- Tauri sidecar lifecycle: start server on app launch, kill on close
- No business logic in the frontend — it is a thin presentation layer
- Graceful handling of server disconnect / restart
