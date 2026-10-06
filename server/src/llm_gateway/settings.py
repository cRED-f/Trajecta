"""Persist the user's LLM runtime preferences in agent_settings.

Key "llm.runtime" holds:

    {"gateway": {"type": "bifrost"},
     "default_provider": "...",
     "default_model": "..."}

Provider credentials are NOT stored here — they live in Bifrost,
which redacts them from management-API responses.

chat.default_model in config is first-run bootstrap only: once this
row exists, the persisted default wins for new conversations.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from server.src.memory.storage.sqlite import SQLiteDatabase

_SETTING_KEY = "llm.runtime"


def _now() -> str:
    return datetime.now(UTC).isoformat()


class LLMSettingsStore:
    """SQLite-backed store of the persisted gateway preference."""

    def __init__(
        self,
        db: SQLiteDatabase,
        *,
        bootstrap_model: str,
    ) -> None:
        self._db = db
        self._bootstrap_model = (
            bootstrap_model.strip()
            or "openai/gpt-4o-mini"
        )

    def _bootstrap(self) -> dict:
        model = self._bootstrap_model
        provider = (
            model.split("/", 1)[0]
            if "/" in model
            else "openai"
        )
        return {
            "gateway": {"type": "bifrost"},
            "default_provider": provider,
            "default_model": model,
        }

    async def get(self) -> dict:
        """Return the runtime preference, falling back to bootstrap."""
        row = await self._db.fetchone(
            """
            SELECT value_json

            FROM agent_settings

            WHERE key = ?
            """,
            (_SETTING_KEY,),
        )

        defaults = self._bootstrap()

        if row is None:
            return defaults

        try:
            stored = json.loads(str(row["value_json"]))
        except json.JSONDecodeError:
            return defaults

        if not isinstance(stored, dict):
            return defaults

        gateway = stored.get("gateway")
        if not isinstance(gateway, dict) or not gateway.get("type"):
            gateway = defaults["gateway"]

        default_provider = (
            str(stored.get("default_provider") or "").strip()
            or defaults["default_provider"]
        )
        default_model = (
            str(stored.get("default_model") or "").strip()
            or defaults["default_model"]
        )

        return {
            "gateway": gateway,
            "default_provider": default_provider,
            "default_model": default_model,
        }

    async def set(
        self,
        *,
        default_provider: str,
        default_model: str,
    ) -> None:
        """Persist the global default (new conversations only)."""
        value = {
            "gateway": {"type": "bifrost"},
            "default_provider": default_provider,
            "default_model": default_model,
        }

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
            (
                _SETTING_KEY,
                json.dumps(value, ensure_ascii=False),
                _now(),
            ),
        )
