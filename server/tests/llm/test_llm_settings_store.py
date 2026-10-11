from __future__ import annotations

from server.src.llm_gateway.settings import LLMSettingsStore
from server.src.memory.storage.sqlite import SQLiteDatabase


async def test_bootstrap_defaults_when_nothing_is_persisted(tmp_path) -> None:
    db = SQLiteDatabase(tmp_path / "settings.db")
    await db.open()

    store = LLMSettingsStore(
        db,
        bootstrap_model="9router/claude-opus-free",
    )
    runtime = await store.get()

    assert runtime == {
        "gateway": {"type": "bifrost"},
        "default_provider": "9router",
        "default_model": "9router/claude-opus-free",
    }


async def test_persisted_default_wins_over_bootstrap(tmp_path) -> None:
    db = SQLiteDatabase(tmp_path / "settings.db")
    await db.open()

    store = LLMSettingsStore(
        db,
        bootstrap_model="9router/claude-opus-free",
    )
    await store.set(
        default_provider="ollama",
        default_model="ollama/llama3.2",
    )

    # A fresh store (same DB, same bootstrap) must see the saved row.
    fresh = LLMSettingsStore(
        db,
        bootstrap_model="9router/claude-opus-free",
    )
    runtime = await fresh.get()

    assert runtime["gateway"] == {"type": "bifrost"}
    assert runtime["default_provider"] == "ollama"
    assert runtime["default_model"] == "ollama/llama3.2"


async def test_corrupt_row_falls_back_to_bootstrap(tmp_path) -> None:
    db = SQLiteDatabase(tmp_path / "settings.db")
    await db.open()

    await db.execute(
        """
        INSERT INTO agent_settings(key, value_json, updated_at)
        VALUES (?, ?, ?)
        """,
        ("llm.runtime", "{not json", "2026-10-07T00:00:00+00:00"),
    )

    store = LLMSettingsStore(
        db,
        bootstrap_model="openai/gpt-4o-mini",
    )
    runtime = await store.get()

    assert runtime["default_model"] == "openai/gpt-4o-mini"
    assert runtime["default_provider"] == "openai"


async def test_set_is_idempotent_and_overwrites(tmp_path) -> None:
    db = SQLiteDatabase(tmp_path / "settings.db")
    await db.open()

    store = LLMSettingsStore(
        db,
        bootstrap_model="9router/claude-opus-free",
    )
    await store.set(
        default_provider="openai",
        default_model="openai/gpt-4o-mini",
    )
    await store.set(
        default_provider="anthropic",
        default_model="anthropic/claude-sonnet-4-6",
    )

    runtime = await store.get()
    assert runtime["default_provider"] == "anthropic"
    assert runtime["default_model"] == "anthropic/claude-sonnet-4-6"

    rows = await db.fetch(
        "SELECT key FROM agent_settings WHERE key = ?",
        ("llm.runtime",),
    )
    assert len(rows) == 1


async def test_provider_free_default_does_not_inherit_old_provider(tmp_path) -> None:
    db = SQLiteDatabase(tmp_path / "settings.db")
    await db.open()
    store = LLMSettingsStore(db, bootstrap_model="9router/claude-opus-free")
    await store.set(default_provider="9router", default_model="route-by-bifrost")
    runtime = await store.get()
    assert runtime["default_model"] == "route-by-bifrost"
    assert runtime["default_provider"] == ""


async def test_provider_free_bootstrap_is_preserved(tmp_path) -> None:
    db = SQLiteDatabase(tmp_path / "settings.db")
    await db.open()
    store = LLMSettingsStore(db, bootstrap_model="route-by-bifrost")
    runtime = await store.get()
    assert runtime["default_provider"] == ""
    assert runtime["default_model"] == "route-by-bifrost"
