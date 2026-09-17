"""Tool route handlers.

GET /api/v1/tools lists Deep Agents built-ins plus Trajecta personal tools and
best-effort MCP discovery.  The endpoint is informational; tool execution stays
inside the agent runtime so policy/checkpoint/trajectory handling cannot be
bypassed by the desktop UI.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from server.src.chat.mcp import MCPToolProvider

router = APIRouter(prefix="/tools", tags=["tools"])


@router.get("")
async def list_tools(request: Request) -> dict:
    provider = request.app.state.personal_tools
    result = provider.catalog()

    # MCPToolProvider caches discovery in each chat runtime. This endpoint uses
    # a short-lived provider only for metadata, keeping failures isolated.
    mcp = MCPToolProvider(request.app.state.settings)
    mcp_tools = await mcp.get_tools()
    result.extend(
        {
            "name": item.name,
            "description": item.description,
            "source": "mcp",
            "enabled": True,
        }
        for item in mcp_tools
    )
    return {"tools": result, "count": len(result)}
