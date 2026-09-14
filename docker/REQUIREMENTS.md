# Docker & Sandboxing Requirements

## Overview

Docker provides isolated execution environments for sensitive tool calls and code execution. The Grafana LGTM stack also runs via Docker Compose for local observability.

## Sandboxed Execution

- Isolated code execution per tool call
- Restricted filesystem (mount only what's needed)
- Resource limits (CPU, memory, disk, timeout)
- Temporary workspaces (created per task, destroyed after)
- Safer shell execution (no host access)
- Network access control (configurable per sandbox)

## Grafana LGTM Stack (Local Dev)

```
docker compose up
```

| Service | Port | Purpose |
|---------|------|---------|
| Grafana | 3000 | Dashboards |
| Tempo | 3200 | Distributed traces |
| Loki | 3100 | Logs |
| Prometheus | 9090 | Metrics |

## File Map

```
docker/
├── REQUIREMENTS.md
├── Dockerfile.sandbox       # Sandbox container for tool execution
├── Dockerfile.observability # Grafana LGTM stack
├── compose.yml              # Docker Compose for observability stack
├── sandbox-config/          # Sandbox resource limits and policies
│   └── default.yaml
└── grafana/
    ├── provisioning/        # Grafana auto-provisioning
    │   ├── datasources/
    │   └── dashboards/
    └── dashboards/          # Pre-built dashboard JSON
        └── agent-overview.json
```

## Key Interfaces

- `create_sandbox(config) -> Sandbox` — provision a new container
- `exec_in_sandbox(sandbox, command) -> ExecResult` — run command in container
- `destroy_sandbox(sandbox)` — tear down container and cleanup
- `list_sandboxes() -> list[Sandbox]` — active sandboxes
