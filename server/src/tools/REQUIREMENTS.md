# Tool Runtime Requirements

## Overview

Two categories of tools: built-in tools (filesystem, shell, git, etc.) and external tools via MCP (Model Context Protocol). All tool execution passes through the guardrail layer. Command execution is restricted by a Windows AppContainer and Job Object; normal host processes still require explicit human approval.

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

## Sandboxed Execution

Deep Agents execute commands use native Windows AppContainer isolation:
- Isolated filesystem
- Job Object CPU, memory and timeout controls (no disk quota)
- Per-workspace AppContainer identity; selected project remains mounted for edits
- Restricted network access where configured

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
└── sandbox/
    ├── __init__.py
    └── native_windows.py # AppContainer adapter for native helper
```

## Key Interfaces

- `execute_tool(name, args, context) -> ToolResult` — unified tool execution
- `list_tools() -> list[ToolDef]` — all available tools (built-in + MCP)
- `sandbox_exec(command, config) -> SandboxResult` — run as restricted native subprocess
- `mcp_connect(server_config) -> MCPClient` — connect to an MCP server
