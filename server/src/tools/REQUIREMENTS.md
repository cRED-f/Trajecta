# Tool Runtime Requirements

## Overview

Two categories of tools: built-in tools (filesystem, shell, git, etc.) and external tools via MCP (Model Context Protocol). All tool execution passes through the guardrail layer. Command execution uses the local shell as the current user, without sandbox isolation. Terminal permissions (ALLOW/ASK/DENY) govern whether it may run.

## Built-in Tools

| Tool | Description |
|------|-------------|
| filesystem_read | Read files and directories |
| filesystem_write | Create and modify files |
| shell | Execute shell commands |
| git | Git operations (status, diff, commit, branch, etc.) |
| python | Execute Python code |
| http | Make HTTP requests |
| project_search | Search project files by content/pattern |
| process | Manage background processes |

## External Tools (MCP)

Integrations via Model Context Protocol:
- GitHub — repo management, issues, PRs
- Databases — query and modify databases
- Browser tools — web browsing and interaction
- Filesystem tools — extended filesystem operations
- Documentation systems — search and read docs
- Custom user tools — user-defined MCP servers

## Local command execution

Deep Agents `execute` runs through `execution/local.py` using PowerShell on Windows or `/bin/sh` on POSIX hosts. It supports a selected working directory, timeouts, process-tree cancellation, and capped tool output. Model-generated commands can read or modify any host resource the user can access; this is **not** a sandbox. The file-transfer API separately restricts virtual `/workspace/` and `/uploads/` paths, but those restrictions do not apply to shell commands. Keep Terminal permission on ASK.

Automatic skill replays requiring `execute` are skipped: unattended host subprocesses are not allowed during evaluation.

## File Map

```
server/src/tools/
├── REQUIREMENTS.md
├── __init__.py
├── builtin/
│   ├── __init__.py
│   ├── filesystem.py     # filesystem_read + filesystem_write
│   ├── shell.py          # shell execution
│   ├── git.py            # git operations
│   ├── python_exec.py    # python code execution
│   ├── http.py           # HTTP requests
│   ├── search.py         # project_search
│   └── process.py        # process management
├── mcp/
│   ├── __init__.py
│   ├── client.py         # MCP client — connect to MCP servers
│   ├── registry.py       # Registry of configured MCP servers
│   └── adapter.py        # Adapt MCP tools to agent tool interface
└── execution/
    ├── __init__.py
    └── local.py # Local system-shell execution
```

## Key Interfaces

- `execute_tool(name, args, context) -> ToolResult` — unified tool execution
- `list_tools() -> list[ToolDef]` — all available tools (built-in + MCP)
- `LocalExecutionBackend.execute(command, timeout)` — run on the host (not sandboxed)
- `mcp_connect(server_config) -> MCPClient` — connect to an MCP server
