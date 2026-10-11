"""Tool route handlers.

GET /api/v1/tools lists Deep Agents built-ins plus Trajecta personal tools and
discovered MCP tools.  The endpoint is informational; tool execution stays
inside the agent runtime so policy/checkpoint/trajectory handling cannot be
bypassed by the desktop UI.

MCP tools are managed through the enable/disable settings API:

    GET    /api/v1/tools/mcp
    POST   /api/v1/tools/mcp/refresh
    PATCH  /api/v1/tools/mcp/servers/{server}
    PATCH  /api/v1/tools/mcp/servers/{server}/tools/{tool}
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from server.src.chat.mcp import MCPToolProvider
from server.src.config import LocalExecutionConfig
from server.src.tools.execution import local_status


router = APIRouter(prefix="/tools", tags=["tools"])


class EnabledRequest(BaseModel):
    enabled: bool


def _mcp(request: Request) -> MCPToolProvider:
    return request.app.state.mcp_tools


@router.get("")
async def list_tools(request: Request) -> dict[str, Any]:
    result = request.app.state.personal_tools.catalog()

    catalog = await _mcp(request).catalog()

    for server in catalog["servers"]:
        for tool in server["tools"]:
            result.append(
                {
                    "name": tool["name"],
                    "description": tool["description"],
                    "source": "mcp",
                    "server": server["name"],
                    "enabled": tool["enabled"],
                }
            )

    return {"tools": result, "count": len(result)}


@router.get("/mcp")
async def mcp_catalog(request: Request) -> dict[str, Any]:
    return await _mcp(request).catalog()


@router.post("/mcp/refresh")
async def refresh_mcp(request: Request) -> dict[str, Any]:
    return await _mcp(request).catalog(refresh=True)


@router.patch("/mcp/servers/{server_name}")
async def update_mcp_server(
    server_name: str,
    body: EnabledRequest,
    request: Request,
) -> dict[str, Any]:
    try:
        return await _mcp(request).set_server_enabled(
            server_name,
            body.enabled,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        ) from exc


@router.patch("/mcp/servers/{server_name}/tools/{tool_name}")
async def update_mcp_tool(
    server_name: str,
    tool_name: str,
    body: EnabledRequest,
    request: Request,
) -> dict[str, Any]:
    try:
        return await _mcp(request).set_tool_enabled(
            server_name,
            tool_name,
            body.enabled,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        ) from exc


@router.get("/receipts/{receipt_id}")
async def get_action_receipt(receipt_id: str, request: Request) -> dict:
    verification = request.app.state.connector_verification

    receipt = await verification.get_receipt(receipt_id)

    if receipt is None:
        raise HTTPException(
            status_code=404,
            detail="Action receipt not found",
        )

    return receipt

@router.get("/execution")
async def execution_status(request: Request) -> dict[str, Any]:
    """Local shell availability (no isolation or enforced CPU/RAM limits)."""
    settings = request.app.state.memory_provider._settings
    status = local_status()
    status["config"] = settings.execution.model_dump()
    status["active"] = bool(settings.execution.enabled and request.app.state.memory_provider.executor is not None)
    return status


@router.patch("/execution")
async def update_execution(body: LocalExecutionConfig, request: Request) -> dict[str, Any]:
    """Persist local execution preferences; execute remains governed by HITL."""
    await request.app.state.permission_policy.set_setting("tools.local_execution", body.model_dump())
    request.app.state.memory_provider.reconfigure_execution(body)
    return await execution_status(request)
