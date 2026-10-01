"""MemoryProvider — assembles Deep Agents Memory tiers from Settings.memory.

Owns the shared aiosqlite connection split:
  - `langgraph.db` → checkpointer (short-term/episodic substrate) + long-term store
  - `trajecta.db`  → Trajecta's own SQLite + FTS + embedded Qdrant

`agent_kwargs()` returns the `create_deep_agent(**...)` dict: checkpointer, store,
backend (CompositeBackend), memory files, skills path. Tier stores are thin
adapters over these primitives (see memory.*.store modules).
"""

from __future__ import annotations

import asyncio
import json
import logging
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
from server.src.memory.embeddings import DEFAULT_OLLAMA_URL
from server.src.memory.episodic.store import EpisodicMemory
from server.src.memory.procedural.store import ProceduralMemory
from server.src.memory.semantic.store import SemanticMemory
from server.src.memory.short_term.store import ShortTermStore
from server.src.memory.storage import SQLiteDatabase, FTSIndex, VectorStore
from server.src.tools.sandbox import DockerSandboxBackend

logger = logging.getLogger(__name__)


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
        self.sandbox: DockerSandboxBackend | None = None

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

        # Deep Agents backend routing. Persistent memories/skills live in the
        # LangGraph Store, user-selected host files are exposed at /workspace/,
        # chat uploads are read-only at /uploads/, and arbitrary execution goes
        # to an isolated Docker sandbox when available.
        ns_local = ("trajecta-local",)

        uploads_root = Path(self._settings.chat.uploads_path).resolve()
        uploads_root.mkdir(parents=True, exist_ok=True)

        workspace_root = Path(self._settings.tools.workspace_root).resolve()
        workspace_root.mkdir(parents=True, exist_ok=True)

        default_backend: Any = StateBackend()
        if self._settings.sandbox.enabled:
            try:
                self.sandbox = DockerSandboxBackend(
                    image=self._settings.sandbox.image,
                    workspace_root=str(workspace_root),
                    uploads_root=str(uploads_root),
                    timeout_seconds=self._settings.sandbox.timeout_seconds,
                    memory_limit=self._settings.sandbox.memory_limit,
                    cpu_limit=self._settings.sandbox.cpu_limit,
                    network_enabled=self._settings.sandbox.network_enabled,
                    auto_remove=self._settings.sandbox.auto_remove,
                )
                default_backend = self.sandbox
            except Exception:
                # Chat must remain usable when Docker is unavailable or the
                # sandbox image has not been built yet. In that case Deep
                # Agents simply omits its built-in `execute` tool.
                logger.exception("Docker sandbox unavailable; using StateBackend")
                self.sandbox = None

        self.backend = CompositeBackend(
            default=default_backend,
            routes={
                "/memories/": StoreBackend(
                    namespace=lambda _rt: ns_local,
                    store=self.store,
                ),
                "/skills/": StoreBackend(
                    namespace=lambda _rt: ns_local + ("skills",),
                    store=self.store,
                ),
                "/workspace/": FilesystemBackend(
                    root_dir=str(workspace_root),
                    virtual_mode=True,
                ),
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

        # Restore the persisted Ollama embedding model BEFORE Qdrant
        # creates/opens collections, so collection dimension matches.
        await self._restore_embedding_configuration()

        if self._settings.memory.vector_store.enabled:
            self.vector.open()

    # -- embedding configuration -----------------------------------------

    @property
    def ollama_embedding_base_url(self) -> str:
        """Ollama base URL from LLM provider settings, else the local default."""

        provider = self._settings.llm.providers.get("ollama")

        if provider is not None and provider.base_url:
            return provider.base_url.rstrip("/")

        return DEFAULT_OLLAMA_URL

    async def _restore_embedding_configuration(self) -> None:
        """Re-apply the embedding model persisted in agent_settings on startup."""

        if self.sqlite is None:
            return

        row = await self.sqlite.fetchone(
            "SELECT value_json FROM agent_settings WHERE key = ?",
            ("memory.embedding",),
        )

        if row is None:
            self.vector.configure_placeholder()
            return

        try:
            config = json.loads(str(row["value_json"]))
        except json.JSONDecodeError:
            self.vector.configure_placeholder()
            return

        if not isinstance(config, dict):
            self.vector.configure_placeholder()
            return

        if config.get("provider") != "ollama":
            self.vector.configure_placeholder()
            return

        model = str(config.get("model") or "").strip()
        dimensions = int(config.get("dimensions") or 0)

        if not model or dimensions <= 0:
            self.vector.configure_placeholder()
            return

        self.vector.configure_ollama(
            base_url=self.ollama_embedding_base_url,
            model=model,
            vector_size=dimensions,
        )

    async def reconfigure_embedding(
        self,
        *,
        model: str,
        dimensions: int,
    ) -> dict[str, int]:
        """Switch embedding model, rebuild Qdrant collections and re-index.

        Changing model usually changes vector dimensions, therefore old
        collections cannot be reused safely.
        """

        if self.sqlite is None:
            raise RuntimeError("MemoryProvider is not open")

        if not self._settings.memory.vector_store.enabled:
            raise RuntimeError("Vector store is disabled")

        self.vector.configure_ollama(
            base_url=self.ollama_embedding_base_url,
            model=model,
            vector_size=dimensions,
        )

        await asyncio.to_thread(
            self.vector.reset_collections,
            ["memories", "episodes", "attachment_chunks"],
        )

        # -------------------------------------------------------------
        # Re-index semantic memories
        # -------------------------------------------------------------

        memories = await self.sqlite.fetch(
            """
            SELECT id, content

            FROM memories

            WHERE tier = 'semantic'
              AND namespace = 'memories'

            ORDER BY updated_at ASC
            """
        )

        memory_items = [
            {
                "doc_id": str(row["id"]),
                "text": str(row["content"]),
            }
            for row in memories
        ]

        memory_count = 0
        for start in range(0, len(memory_items), 32):
            memory_count += await asyncio.to_thread(
                self.vector.upsert_many,
                "memories",
                memory_items[start : start + 32],
            )

        # -------------------------------------------------------------
        # Re-index attachment RAG chunks
        # -------------------------------------------------------------

        chunks = await self.sqlite.fetch(
            """
            SELECT
                c.id,
                c.attachment_id,
                c.conversation_id,
                c.chunk_index,
                c.start_char,
                c.end_char,
                c.content,
                a.filename

            FROM attachment_chunks AS c

            LEFT JOIN attachments AS a
            ON a.id = c.attachment_id

            ORDER BY c.conversation_id, c.attachment_id, c.chunk_index
            """
        )

        chunk_items = [
            {
                "doc_id": str(row["id"]),
                "text": str(row["content"]),
                "payload": {
                    "attachment_id": row["attachment_id"],
                    "conversation_id": row["conversation_id"],
                    "chunk_index": row["chunk_index"],
                    "filename": row.get("filename"),
                    "start_char": row["start_char"],
                    "end_char": row["end_char"],
                },
            }
            for row in chunks
        ]

        attachment_chunk_count = 0
        for start in range(0, len(chunk_items), 32):
            attachment_chunk_count += await asyncio.to_thread(
                self.vector.upsert_many,
                "attachment_chunks",
                chunk_items[start : start + 32],
            )

        return {
            "memories": memory_count,
            "attachment_chunks": attachment_chunk_count,
        }

    async def close(self) -> None:
        self.vector.close()
        if self.sandbox is not None:
            self.sandbox.close()
            self.sandbox = None
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

    def agent_kwargs(self, *, allow_execute: bool = True) -> dict[str, Any]:
        """Keyword arguments for `create_deep_agent(model, **agent_kwargs())`.

        With ``allow_execute=False`` (Terminal permission = DENY) the runtime
        backend is replaced by a StateBackend that has no ``execute`` tool, so
        Deep Agents does not surface local process execution to the model.
        """
        if (
            self.checkpointer is None
            or self.store is None
            or self.backend is None
        ):
            raise RuntimeError(
                "MemoryProvider is not open — call await provider.open() first"
            )

        backend = self.backend if allow_execute else self._backend_without_execute()

        return {
            "checkpointer": self.checkpointer,
            "store": self.store,
            "backend": backend,
            "memory": self.memory_files,
            "skills": [self.skills_path],
        }

    def _backend_without_execute(self) -> CompositeBackend:
        """Runtime backend used when Terminal permission is DENY.

        StateBackend has no ``execute`` capability, so Deep Agents does not
        create the built-in ``execute`` tool. The route backends mirror ``open()``
        but never include the Docker sandbox default.
        """
        if (
            self.store is None
            or self.checkpointer is None
        ):
            raise RuntimeError(
                "MemoryProvider is not open — call await provider.open() first"
            )

        ns_local = ("trajecta-local",)

        uploads_root = Path(self._settings.chat.uploads_path).resolve()
        workspace_root = Path(self._settings.tools.workspace_root).resolve()

        return CompositeBackend(
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
                "/workspace/": FilesystemBackend(
                    root_dir=str(workspace_root),
                    virtual_mode=True,
                ),
                "/uploads/": FilesystemBackend(
                    root_dir=str(uploads_root),
                    virtual_mode=True,
                ),
            },
        )


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