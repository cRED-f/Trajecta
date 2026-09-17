"""Persist user MCP preferences.

Defaults:

    server = enabled
    tool   = enabled

Explicit user preferences override those defaults.
"""

from __future__ import annotations

from datetime import UTC, datetime

from server.src.memory.storage.sqlite import SQLiteDatabase


def _now() -> str:
    return datetime.now(UTC).isoformat()


class MCPToolSettingsStore:
    """SQLite-backed store of per-server and per-tool MCP enable/disable state."""

    def __init__(self, db: SQLiteDatabase) -> None:
        self._db = db

    async def set_server_enabled(
        self,
        server_name: str,
        enabled: bool,
    ) -> None:
        await self._db.execute(
            """
            INSERT INTO mcp_server_preferences(
                server_name,
                enabled,
                updated_at
            )
            VALUES(?, ?, ?)

            ON CONFLICT(server_name)
            DO UPDATE SET
                enabled = excluded.enabled,
                updated_at = excluded.updated_at
            """,
            (
                server_name,
                1 if enabled else 0,
                _now(),
            ),
        )

    async def set_tool_enabled(
        self,
        server_name: str,
        tool_name: str,
        enabled: bool,
    ) -> None:
        await self._db.execute(
            """
            INSERT INTO mcp_tool_preferences(
                server_name,
                tool_name,
                enabled,
                updated_at
            )
            VALUES(?, ?, ?, ?)

            ON CONFLICT(server_name, tool_name)
            DO UPDATE SET
                enabled = excluded.enabled,
                updated_at = excluded.updated_at
            """,
            (
                server_name,
                tool_name,
                1 if enabled else 0,
                _now(),
            ),
        )

    async def server_states(self) -> dict[str, bool]:
        rows = await self._db.fetch(
            """
            SELECT server_name, enabled
            FROM mcp_server_preferences
            """
        )
        return {
            str(row["server_name"]): bool(row["enabled"])
            for row in rows
        }

    async def tool_states(self) -> dict[tuple[str, str], bool]:
        rows = await self._db.fetch(
            """
            SELECT server_name, tool_name, enabled
            FROM mcp_tool_preferences
            """
        )
        return {
            (str(row["server_name"]), str(row["tool_name"])): bool(row["enabled"])
            for row in rows
        }

    async def effective_states(
        self,
        tools: list[tuple[str, str]],
    ) -> dict[tuple[str, str], bool]:
        server_states = await self.server_states()
        tool_states = await self.tool_states()

        result: dict[tuple[str, str], bool] = {}

        for server, tool in tools:
            server_enabled = server_states.get(server, True)
            tool_enabled = tool_states.get((server, tool), True)
            result[(server, tool)] = server_enabled and tool_enabled

        return result