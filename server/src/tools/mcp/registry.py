"""Configured MCP server registry with isolated discovery failures."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from langchain_core.tools import BaseTool

from server.src.config import Settings
from server.src.tools.mcp.client import MCPClient

logger = logging.getLogger(__name__)


class MCPRegistry:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def servers(self) -> dict[str, dict[str, Any]]:
        return dict(self.settings.tools.mcp_servers)

    async def discover(self) -> list[BaseTool]:
        discovered: list[BaseTool] = []
        for name, config in self.servers().items():
            try:
                tools = await asyncio.wait_for(
                    MCPClient(name, config).list_tools(),
                    timeout=self.settings.tools.discovery_timeout_seconds,
                )
                discovered.extend(tools)
            except Exception:
                logger.exception("Failed to discover MCP server %s", name)
        return discovered
