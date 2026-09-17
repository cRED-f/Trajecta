"""Single-server MCP client using LangChain's current MCPAdapter."""

from __future__ import annotations

from typing import Any

from langchain.mcp import MCPAdapter
from langchain_core.tools import BaseTool


class MCPClient:
    def __init__(self, name: str, config: dict[str, Any]) -> None:
        self.name = name
        self.config = config

    async def list_tools(self) -> list[BaseTool]:
        async with MCPAdapter({"mcpServers": {self.name: self.config}}) as adapter:
            return await adapter.list_tools(cache_mode="use")
