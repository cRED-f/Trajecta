"""Deterministic permission engine.

User-controlled permission modes (allow / ask / deny) drive what the agent may
do. A ``DENY`` removes the capability entirely (the tool is filtered out of the
next agent run), ``ASK`` keeps the tool but pauses for human approval through
Deep Agents HITL, and ``ALLOW`` executes normally.

The engine persists permission modes and free-form agent settings in SQLite
(schema v11) via :class:`PermissionPolicyStore`.
"""

from __future__ import annotations

import json

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from server.src.memory.storage.sqlite import SQLiteDatabase


PermissionMode = Literal["allow", "ask", "deny"]

_VALID_MODES = {"allow", "ask", "deny"}


def _now() -> str:
    return datetime.now(UTC).isoformat()


PERMISSIONS: dict[str, dict[str, str]] = {
    "filesystem-write": {
        "title": "Modify local files",
        "description": "Create, edit, move and delete files in the workspace.",
        "default": "ask",
    },
    "terminal": {
        "title": "Run commands",
        "description": "Execute sandbox commands and local processes.",
        "default": "ask",
    },
    "browser-actions": {
        "title": "Browser actions",
        "description": "Navigate, click, type, upload and submit through the browser.",
        "default": "ask",
    },
    "computer-control": {
        "title": "Control computer",
        "description": "Use mouse, keyboard, clipboard and desktop applications.",
        "default": "ask",
    },
    "external-communication": {
        "title": "Send messages",
        "description": "Send email, messages or other communication through connected services.",
        "default": "ask",
    },
    "network-mutations": {
        "title": "Modify online services",
        "description": "Perform API or connector actions that modify remote state.",
        "default": "ask",
    },
}

# Tool -> permission category. Only tools listed here are governed by the
# deterministic policy; anything absent is left to MCP enable/disable and the
# tool-specific HITL coverage in the provider.
TOOL_PERMISSION_GROUPS: dict[str, str] = {
    # ---------------------------------
    # Terminal / processes
    # ---------------------------------
    "process_start": "terminal",
    "process_input": "terminal",
    "process_stop": "terminal",
    # Deep Agents built-in sandbox execution.
    "execute": "terminal",
    # ---------------------------------
    # Browser mutations
    # ---------------------------------
    "browser_navigate": "browser-actions",
    "browser_click": "browser-actions",
    "browser_type": "browser-actions",
    "browser_press": "browser-actions",
    "browser_download": "browser-actions",
    "browser_upload": "browser-actions",
    "browser_new_tab": "browser-actions",
    "browser_close_tab": "browser-actions",
    "browser_submit_and_verify": "browser-actions",
    # ---------------------------------
    # Desktop control
    # ---------------------------------
    "computer_click": "computer-control",
    "computer_move": "computer-control",
    "computer_drag": "computer-control",
    "computer_scroll": "computer-control",
    "computer_type": "computer-control",
    "computer_key": "computer-control",
    "computer_shortcut": "computer-control",
    "clipboard_write": "computer-control",
    "wake_on_lan": "computer-control",
    # ---------------------------------
    # Local mutating tools
    # ---------------------------------
    "sqlite_execute": "filesystem-write",
    "archive_extract": "filesystem-write",
    "archive_create": "filesystem-write",
    "image_transform": "filesystem-write",
    "media_convert": "filesystem-write",
    "local_text_to_speech": "filesystem-write",
    # ---------------------------------
    # Generic network mutation
    # ---------------------------------
    "http_request": "network-mutations",
    "verified_http_mutation": "network-mutations",
}


class PermissionSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    modes: dict[str, PermissionMode]

    def mode(self, permission_id: str) -> PermissionMode:
        return self.modes.get(permission_id, "ask")


class PermissionPolicyStore:
    """Persist and read user permission modes and agent settings."""

    def __init__(self, db: SQLiteDatabase) -> None:
        self._db = db

    async def snapshot(self) -> PermissionSnapshot:
        rows = await self._db.fetch(
            """
            SELECT
                permission_id,
                mode

            FROM agent_permissions
            """
        )

        persisted = {
            str(row["permission_id"]): str(row["mode"])
            for row in rows
        }

        modes: dict[str, PermissionMode] = {}

        for permission_id, config in PERMISSIONS.items():
            value = persisted.get(permission_id, str(config["default"]))

            if value not in _VALID_MODES:
                value = "ask"

            modes[permission_id] = value  # type: ignore[assignment]

        return PermissionSnapshot(modes=modes)

    async def list_permissions(self) -> list[dict]:
        snapshot = await self.snapshot()

        return [
            {
                "id": permission_id,
                "title": config["title"],
                "description": config["description"],
                "mode": snapshot.mode(permission_id),
            }
            for permission_id, config in PERMISSIONS.items()
        ]

    async def set_mode(self, permission_id: str, mode: PermissionMode) -> None:
        if permission_id not in PERMISSIONS:
            raise ValueError(f"Unknown permission: {permission_id}")

        if mode not in _VALID_MODES:
            raise ValueError("mode must be allow, ask or deny")

        await self._db.execute(
            """
            INSERT INTO agent_permissions(
                permission_id,
                mode,
                updated_at
            )

            VALUES (?, ?, ?)

            ON CONFLICT(permission_id)
            DO UPDATE SET
                mode = excluded.mode,
                updated_at = excluded.updated_at
            """,
            (permission_id, mode, _now()),
        )

    async def get_setting(self, key: str, default=None):
        row = await self._db.fetchone(
            """
            SELECT value_json

            FROM agent_settings

            WHERE key = ?
            """,
            (key,),
        )

        if row is None:
            return default

        try:
            return json.loads(str(row["value_json"]))
        except json.JSONDecodeError:
            return default

    async def set_setting(self, key: str, value) -> None:
        await self._db.execute(
            """
            INSERT INTO agent_settings(
                key,
                value_json,
                updated_at
            )

            VALUES (?, ?, ?)

            ON CONFLICT(key)
            DO UPDATE SET
                value_json = excluded.value_json,
                updated_at = excluded.updated_at
            """,
            (key, json.dumps(value, ensure_ascii=False), _now()),
        )