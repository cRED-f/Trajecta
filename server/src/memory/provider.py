"""MemoryProvider — assembles Deep Agents Memory tiers from Settings.memory.

Owns the shared aiosqlite connection split:
  - `langgraph.db` → checkpointer (short-term/episodic substrate) + long-term store
  - `trajecta.db`  → Trajecta's own SQLite + FTS + embedded Qdrant

`agent_kwargs()` returns the `create_deep_agent(**...)` dict: checkpointer, store,
backend (CompositeBackend), memory files, skills path. Tier stores are thin
adapters over these primitives (see memory.*.store modules).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import aiosqlite

from deepagents.backends import (
    CompositeBackend,
    FilesystemBackend,
    StateBackend,
    StoreBackend,
)

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.store.sqlite.aio import AsyncSqliteStore

from server.src.config import Settings
from server.src.memory.episodic.store import EpisodicMemory
from server.src.memory.procedural.store import ProceduralMemory
from server.src.memory.semantic.store import SemanticMemory
from server.src.memory.short_term.store import ShortTermStore
from server.src.memory.storage import SQLiteDatabase, FTSIndex, VectorStore


class MemoryProvider:
    """Unified access point for all Deep Agents Memory tiers."""

    def __init__(self, settings: Settings | None = None, *, sdk_client: Any | None = None) -> None:
        self._settings = settings or Settings.load()
        cfg = self._settings.memory

        self._conn_saver: aiosqlite.Connection | None = None
        self._conn: aiosqlite.Connection | None = None

        self.checkpointer: AsyncSqliteSaver | None = None
        self.store: AsyncSqliteStore | None = None
        self.backend: CompositeBackend | None = None

        self.db_path = cfg.db_path
        self.langgraph_db_path = cfg.langgraph_db_path
        self.memory_files = cfg.memory_files
        self.skills_path = cfg.skills_path

        self.sqlite: SQLiteDatabase | None = None
        self.fts: FTSIndex | None = None

        self.short_term = ShortTermStore(self)
        self.semantic = SemanticMemory(self)
        self.episodic = EpisodicMemory(self, sdk_client=sdk_client)
        self.procedural = ProceduralMemory(self)
        self.vector = VectorStore(
            cfg.vector_store.path,
            collection_prefix=cfg.vector_store.collection_prefix,
        )

    # -- lifecycle ---------------------------------------------------------

    async def open(self) -> None:
        """Open the SQLite connections (two per langgraph.db: saver + store each need their own BEGIN)."""
        if self._conn is not None:
            return  # already open
        fs = Path(self.langgraph_db_path)
        fs.parent.mkdir(parents=True, exist_ok=True)

        # Two aiosqlite connections to the same file (WAL mode handles concurrency).
        # Saver uses default isolation_level (implicit transactions for checkpointer).
        # Store uses isolation_level=None so it controls BEGIN/COMMIT explicitly
        # — matches how LangGraph itself constructs AsyncSqliteStore.
        conn_saver = await aiosqlite.connect(self.langgraph_db_path)
        conn_saver.row_factory = sqlite3.Row
        self._conn_saver = conn_saver

        conn_store = await aiosqlite.connect(self.langgraph_db_path, isolation_level=None)
        conn_store.row_factory = sqlite3.Row
        self._conn = conn_store  # close() below cleans up this one

        self.checkpointer = AsyncSqliteSaver(conn_saver)
        self.store = AsyncSqliteStore(conn_store)
        await self.store.setup()

        # Deep Agents backend routes /memories/ and /skills/ into the store,
        # everything else into thread-scoped state.
        # Namespaces that ignore their Runtime arg work outside graph context.
        ns_local = ("trajecta-local",)

        uploads_root = Path(
            self._settings.chat.uploads_path
        ).resolve()

        uploads_root.mkdir(
            parents=True,
            exist_ok=True,
        )

        self.backend = CompositeBackend(
            default=StateBackend(),
            routes={
                "/memories/": StoreBackend(
                    namespace=lambda _rt: ns_local,
                    store=self.store,
                ),

                "/skills/": StoreBackend(
                    namespace=lambda _rt: ns_local + ("skills",),
                    store=self.store,
                ),

                # Files uploaded by the desktop app.
                #
                # CompositeBackend strips "/uploads/" before
                # passing the path to FilesystemBackend.
                "/uploads/": FilesystemBackend(
                    root_dir=str(uploads_root),
                    virtual_mode=True,
                ),
            },
        )

        # Trajecta's own stores: SQLite + FTS always on (even with Qdrant off);
        # Qdrant is embedded and resilient (no-op if unavailable).
        self.sqlite = SQLiteDatabase(self.db_path)
        await self.sqlite.open()
        self.fts = FTSIndex(self.sqlite)
        await self.fts.open()
        if self._settings.memory.vector_store.enabled:
            self.vector.open()

    async def close(self) -> None:
        self.vector.close()
        if self.fts is not None:
            self.fts = None
        if self.sqlite is not None:
            await self.sqlite.close()
            self.sqlite = None
        for attr in ("_conn_saver", "_conn"):
            conn = getattr(self, attr, None)
            if conn is not None:
                try:
                    await conn.close()
                except Exception:
                    pass
                setattr(self, attr, None)
        self.checkpointer = None
        self.store = None
        self.backend = None

    # -- agent runtime interface ------------------------------------------

    def agent_kwargs(self) -> dict[str, Any]:
        """Keyword arguments for `create_deep_agent(model, **agent_kwargs())`."""
        if (
            self.checkpointer is None
            or self.store is None
            or self.backend is None
        ):
            raise RuntimeError(
                "MemoryProvider is not open — call await provider.open() first"
            )

        return {
            "checkpointer": self.checkpointer,
            "store": self.store,
            "backend": self.backend,
            "memory": self.memory_files,
            "skills": [self.skills_path],
        }


# ---------------------------------------------------------------------------
# Module-level singleton (created on demand, reset via reset_memory_provider)
# ---------------------------------------------------------------------------

_provider: MemoryProvider | None = None


def get_memory_provider(settings: Settings | None = None) -> MemoryProvider:
    """Return the shared MemoryProvider, opening it if needed."""
    global _provider
    if _provider is None:
        _provider = MemoryProvider(settings)
    return _provider


async def reset_memory_provider() -> None:
    """Close the shared provider and drop the singleton (used by tests/teardown)."""
    global _provider
    if _provider is not None:
        await _provider.close()
        _provider = None