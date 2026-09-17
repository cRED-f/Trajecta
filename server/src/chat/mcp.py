from __future__ import annotations

import asyncio
import logging

from typing import Any

from langchain_core.tools import BaseTool
from langchain.mcp import MCPAdapter

from server.src.chat.mcp_settings import MCPToolSettingsStore
from server.src.config import Settings
from server.src.tools.verification import ConnectorVerificationService


logger = logging.getLogger(__name__)


class MCPToolProvider:
    """
    Discover MCP tools and apply user enable/disable preferences.

    All discovered tools are cached.

    get_tools()
        → only enabled tools

    catalog()
        → enabled + disabled tools
    """

    def __init__(
        self,
        settings: Settings,
        preferences: MCPToolSettingsStore,
        verification: ConnectorVerificationService | None = None,
    ) -> None:
        self._settings = settings
        self._preferences = preferences
        self._verification = verification

        self._all_tools: list[BaseTool] | None = None
        self._server_status: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    # -------------------------------------------------
    # Agent tool access
    # -------------------------------------------------

    async def get_tools(
        self,
        *,
        refresh: bool = False,
    ) -> list[BaseTool]:
        """Return only tools currently enabled by the user."""
        tools = await self.get_all_tools(refresh=refresh)
        if not tools:
            return []

        states = await self._preferences.effective_states(
            [(self._server_name(tool), tool.name) for tool in tools]
        )

        return [
            tool
            for tool in tools
            if states.get((self._server_name(tool), tool.name), True)
        ]

    async def get_all_tools(
        self,
        *,
        refresh: bool = False,
    ) -> list[BaseTool]:
        if self._all_tools is not None and not refresh:
            return list(self._all_tools)

        async with self._lock:
            if self._all_tools is not None and not refresh:
                return list(self._all_tools)

            tools = await self._discover()

            self._all_tools = tools

            return list(tools)

    # -------------------------------------------------
    # Settings UI catalog
    # -------------------------------------------------

    async def catalog(
        self,
        *,
        refresh: bool = False,
    ) -> dict[str, Any]:
        tools = await self.get_all_tools(refresh=refresh)

        if tools:
            effective = await self._preferences.effective_states(
                [(self._server_name(tool), tool.name) for tool in tools]
            )
        else:
            effective = {}

        server_states = await self._preferences.server_states()

        grouped: dict[str, list[dict[str, Any]]] = {
            name: [] for name in self._settings.tools.mcp_servers
        }

        for tool in tools:
            server = self._server_name(tool)

            grouped.setdefault(server, []).append(
                {
                    "server": server,
                    "name": tool.name,
                    "description": tool.description or "",
                    "enabled": effective.get((server, tool.name), True),
                }
            )

        servers: list[dict[str, Any]] = []
        total_tools = 0
        enabled_tools = 0

        for server_name in self._settings.tools.mcp_servers:
            items = sorted(
                grouped.get(server_name, []),
                key=lambda item: item["name"].lower(),
            )

            total_tools += len(items)
            enabled_count = sum(1 for item in items if item["enabled"])
            enabled_tools += enabled_count

            status = self._server_status.get(
                server_name,
                {"status": "unknown", "error": None},
            )

            servers.append(
                {
                    "name": server_name,
                    "enabled": server_states.get(server_name, True),
                    "status": status.get("status", "unknown"),
                    "error": status.get("error"),
                    "tool_count": len(items),
                    "enabled_count": enabled_count,
                    "tools": items,
                }
            )

        return {
            "servers": servers,
            "server_count": len(servers),
            "tool_count": total_tools,
            "enabled_count": enabled_tools,
        }

    async def set_server_enabled(
        self,
        server_name: str,
        enabled: bool,
    ) -> dict[str, Any]:
        self._validate_server(server_name)
        await self._preferences.set_server_enabled(server_name, enabled)
        return await self.catalog()

    async def set_tool_enabled(
        self,
        server_name: str,
        tool_name: str,
        enabled: bool,
    ) -> dict[str, Any]:
        self._validate_server(server_name)

        tools = await self.get_all_tools()

        exists = any(
            self._server_name(tool) == server_name and tool.name == tool_name
            for tool in tools
        )

        if not exists:
            raise ValueError(
                f"MCP tool {server_name}.{tool_name} was not found"
            )

        await self._preferences.set_tool_enabled(server_name, tool_name, enabled)
        return await self.catalog()

    # -------------------------------------------------
    # Discovery
    # -------------------------------------------------

    async def _discover(self) -> list[BaseTool]:
        servers = self._settings.tools.mcp_servers
        self._server_status = {}

        if not servers:
            return []

        discovered: list[BaseTool] = []

        for name, server_config in servers.items():
            try:
                tools = await asyncio.wait_for(
                    self._load_server(name, server_config),
                    timeout=self._settings.tools.discovery_timeout_seconds,
                )

                for tool in tools:
                    metadata = tool.metadata or {}
                    trajecta = metadata.get("trajecta")
                    if not isinstance(trajecta, dict):
                        trajecta = {}
                    tool.metadata = {
                        **metadata,
                        "trajecta": {
                            **trajecta,
                            "mcp_server": name,
                        },
                    }

                discovered.extend(tools)

                self._server_status[name] = {
                    "status": "connected",
                    "error": None,
                    "tool_count": len(tools),
                }

                logger.info("Loaded %s MCP tools from %s", len(tools), name)

            except Exception as exc:
                self._server_status[name] = {
                    "status": "error",
                    "error": f"{type(exc).__name__}: {exc}",
                    "tool_count": 0,
                }

                logger.exception("Failed to load MCP server %s", name)

        if self._verification is not None:
            discovered = self._verification.wrap_connector_tools(discovered)

        return discovered

    async def _load_server(
        self,
        name: str,
        server_config: dict[str, Any],
    ) -> list[BaseTool]:
        # langchain.mcp.MCPAdapter supports the mcpServers shape.
        config = {
            "mcpServers": {
                name: server_config,
            }
        }

        async with MCPAdapter(config) as adapter:
            return await adapter.list_tools(cache_mode="use")

    # -------------------------------------------------
    # Helpers
    # -------------------------------------------------

    def _validate_server(self, server_name: str) -> None:
        if server_name not in self._settings.tools.mcp_servers:
            raise ValueError(f"MCP server {server_name!r} is not configured")

    @staticmethod
    def _server_name(tool: BaseTool) -> str:
        metadata = tool.metadata or {}

        trajecta = metadata.get("trajecta")
        if isinstance(trajecta, dict):
            # Wrapped verification tools overwrite the trajecta namespace with
            # `connector`; raw discoveries carry `connector_server`/`mcp_server`.
            for key in ("connector", "connector_server", "mcp_server"):
                name = trajecta.get(key)
                if name:
                    return str(name)

        mcp = metadata.get("mcp")
        if isinstance(mcp, dict):
            server = mcp.get("server")
            if isinstance(server, dict):
                name = server.get("name")
                if name:
                    return str(name)

        return "unknown"