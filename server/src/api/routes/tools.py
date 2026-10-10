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
from server.src.config import SandboxConfig
from server.src.tools.sandbox import native_status


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

@router.get("/sandbox")
async def sandbox_status(request: Request) -> dict[str, Any]:
    """Expose actual native execution readiness, never report simulated isolation."""
    settings = request.app.state.settings
    status = native_status()
    status["config"] = settings.sandbox.model_dump()
    status["active"] = bool(
        settings.sandbox.enabled
        and request.app.state.memory_provider.sandbox is not None
    )
    if status["available"] and not status["active"] and settings.sandbox.enabled:
        status["message"] = (
            "Launcher found, but AppContainer setup or workspace permissions failed. "
            "Check backend logs; unisolated execution is blocked."
        )
    return status


@router.patch("/sandbox")
async def update_sandbox(body: SandboxConfig, request: Request) -> dict[str, Any]:
    """Persist safe resource controls; all command execution remains HITL-governed."""
    if body.cpu_limit <= 0 or body.cpu_limit > 64:
        raise HTTPException(status_code=422, detail="CPU limit must be within (0, 64] cores")
    if body.timeout_seconds < 1 or body.timeout_seconds > 3600:
        raise HTTPException(status_code=422, detail="Timeout must be 1-3600 seconds")
    try:
        from server.src.tools.sandbox.native_windows import _memory_bytes
        _memory_bytes(body.memory_limit)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await request.app.state.permission_policy.set_setting("tools.native_sandbox", body.model_dump())
    request.app.state.memory_provider.reconfigure_sandbox(body)
    return await sandbox_status(request)
