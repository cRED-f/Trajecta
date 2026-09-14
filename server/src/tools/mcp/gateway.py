"""MCP gateway — connects the Python app to Bifrost's /mcp endpoint.

Bifrost proxies MCP tools (GitHub, Browser, DB, etc.) behind a single
OpenAI-compatible HTTP server. Python uses langchain-mcp-adapters to discover
them as standard LangChain tools — no Go imports needed.

Run: python -m server.src.tools.mcp.verify
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from langchain_mcp_adapters.client import MultiServerMCPClient


class MCPGateway:
    """Wraps Bifrost's /mcp endpoint as a LangChain tool source.

    Call `bind()` to get tools, which lazily connects to Bifrost and returns
    a list of LangChain BaseTools.
    """

    def __init__(self, base_url: str | None = None, virtual_key: str | None = None) -> None:
        self._base_url = base_url or os.environ.get("BIFROST_URL", "http://127.0.0.1:8080")
        self._virtual_key = virtual_key or os.environ.get("BIFROST_VIRTUAL_KEY", "sk-bf-local")
        self._client: MultiServerMCPClient | None = None

    @property
    def mcp_url(self) -> str:
        return f"{self._base_url.rstrip('/')}/mcp"

    async def connect(self) -> MultiServerMCPClient:
        from langchain_mcp_adapters.client import MultiServerMCPClient

        self._client = MultiServerMCPClient(
            {
                "bifrost": {
                    "transport": "http",
                    "url": self.mcp_url,
                    "headers": {
                        "Authorization": f"Bearer {self._virtual_key}",
                    },
                }
            }
        )
        return self._client

    async def get_tools(self) -> list[Any]:
        """Return registered MCP tools as LangChain BaseTools."""
        client = self._client or await self.connect()
        return await client.get_tools()

    async def close(self) -> None:
        if self._client is not None:
            await self._client.__aexit__(None, None, None)
            self._client = None