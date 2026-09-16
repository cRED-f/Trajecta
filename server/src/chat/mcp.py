from __future__ import annotations

import asyncio
import logging

from typing import Any

from langchain_core.tools import BaseTool
from langchain.mcp import MCPAdapter

from server.src.config import Settings


logger = logging.getLogger(__name__)


class MCPToolProvider:
    """
    Discover MCP tools for Trajecta.

    Each configured MCP server is loaded independently so one dead
    integration does not disable every MCP tool.
    """

    def __init__(
        self,
        settings: Settings,
    ) -> None:
        self._settings = settings

        self._tools: list[BaseTool] | None = None

        self._lock = asyncio.Lock()

    async def get_tools(
        self,
        *,
        refresh: bool = False,
    ) -> list[BaseTool]:
        if (
            self._tools is not None
            and not refresh
        ):
            return list(self._tools)

        async with self._lock:
            if (
                self._tools is not None
                and not refresh
            ):
                return list(self._tools)

            tools = await self._discover()

            self._tools = tools

            return list(tools)

    async def _discover(
        self,
    ) -> list[BaseTool]:
        servers = (
            self._settings.tools.mcp_servers
        )

        if not servers:
            return []

        discovered: list[BaseTool] = []

        for name, server_config in servers.items():
            try:
                tools = await asyncio.wait_for(
                    self._load_server(
                        name,
                        server_config,
                    ),
                    timeout=(
                        self._settings
                        .tools
                        .discovery_timeout_seconds
                    ),
                )

                discovered.extend(tools)

                logger.info(
                    "Loaded %s MCP tools from %s",
                    len(tools),
                    name,
                )

            except Exception:
                # One broken MCP integration must not make
                # Trajecta chat unavailable.
                logger.exception(
                    "Failed to load MCP server %s",
                    name,
                )

        return discovered

    async def _load_server(
        self,
        name: str,
        server_config: dict[str, Any],
    ) -> list[BaseTool]:
        # langchain.mcp.MCPConfig supports the mcpServers shape.
        config = {
            "mcpServers": {
                name: server_config,
            }
        }

        async with MCPAdapter(
            config
        ) as adapter:
            return await adapter.list_tools(
                cache_mode="use"
            )
