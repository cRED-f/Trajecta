"""MCP tools returned by LangChain MCPAdapter are already BaseTool objects."""

from __future__ import annotations

from langchain_core.tools import BaseTool


class MCPToolAdapter:
    @staticmethod
    def adapt(tool: BaseTool) -> BaseTool:
        return tool

    @staticmethod
    def adapt_all(tools: list[BaseTool]) -> list[BaseTool]:
        return list(tools)
