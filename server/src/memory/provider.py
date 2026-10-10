"""MemoryProvider — assembles Deep Agents Memory tiers from Settings.memory.

Owns the shared aiosqlite connection split:
  - `langgraph.db` → checkpointer (conversation state) + long-term store
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

from server.src.config import Settings, SandboxConfig
from server.src.memory.embeddings import DEFAULT_OLLAMA_URL
from server.src.memory.episodic.store import EpisodicMemory
from server.src.memory.procedural.store import ProceduralMemory
from server.src.memory.semantic.store import SemanticMemory
from server.src.memory.short_term.store import ShortTermStore
from server.src.memory.storage import SQLiteDatabase, FTSIndex, VectorStore
from server.src.tools.sandbox import NativeWindowsSandboxBackend, NativeSandboxUnavailable

logger = logging.getLogger(__name__)


class MemoryProvider:
    """Unified access point for all Deep Agents Memory tiers."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or Settings.load()
        cfg = self._settings.memory

        self._conn_saver: aiosqlite.Connection | None = None
        self._conn: aiosqlite.Connection | None = None

        self.checkpointer: AsyncSqliteSaver | None = None
        self.store: AsyncSqliteStore | None = None
        self.backend: CompositeBackend | None = None
        self.sandbox: NativeWindowsSandboxBackend | None = None

        # Host folders pinned per conversation. Backends and sandbox
        # containers are derived from these instead of mutating settings,
        # so two conversations can point at different projects at once.
        self._default_workspace_root: str | None = None
        self._uploads_root: Path | None = None
        self._sandboxes: dict[str, NativeWindowsSandboxBackend] = {}

        self.db_path = cfg.db_path
        self.langgraph_db_path = cfg.langgraph_db_path
        self.memory_files = cfg.memory_files
        self.skills_path = cfg.skills_path

        self.sqlite: SQLiteDatabase | None = None
        self.fts: FTSIndex | None = None

        self.short_term = ShortTermStore(self)
        self.semantic = SemanticMemory(self)
        self.episodic = EpisodicMemory(self)
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
        # to a restricted native Windows AppContainer when available.
        ns_local = ("trajecta-local",)

        uploads_root = Path(self._settings.chat.uploads_path).resolve()
        uploads_root.mkdir(parents=True, exist_ok=True)
        self._uploads_root = uploads_root

        workspace_root = Path(self._settings.tools.workspace_root).resolve()
        workspace_root.mkdir(parents=True, exist_ok=True)
        self._default_workspace_root = str(workspace_root)

        # Trajecta's own stores: SQLite + FTS always on (even with Qdrant off);
        # Qdrant is embedded and resilient (no-op if unavailable).
        self.sqlite = SQLiteDatabase(self.db_path)
        await self.sqlite.open()
        self.fts = FTSIndex(self.sqlite)
        await self.fts.open()

        # Runtime sandbox controls survive a backend restart, like permission settings.
        await self._restore_sandbox_settings()
        self.sandbox = self._sandbox_for(str(workspace_root))
        self.backend = self._build_backend(str(workspace_root), allow_execute=True)

        # Restore the persisted Ollama embedding model BEFORE Qdrant
        # creates/opens collections, so collection dimension matches.
        await self._restore_embedding_configuration()

        if self._settings.memory.vector_store.enabled:
            self.vector.open()

    async def _restore_sandbox_settings(self) -> None:
        if self.sqlite is None:
            return
        row = await self.sqlite.fetchone(
            "SELECT value_json FROM agent_settings WHERE key = ?", ("tools.native_sandbox",)
        )
        if row is not None:
            try:
                self._settings.sandbox = SandboxConfig.model_validate(json.loads(row["value_json"]))
            except (ValueError, TypeError):
                logger.warning("Invalid saved sandbox settings; using safe defaults")

    def reconfigure_sandbox(self, config: SandboxConfig) -> None:
        """Apply newly persisted settings to subsequent agent turns; revoke old ACLs."""
        self._settings.sandbox = config
        for sandbox in list(self._sandboxes.values()):
            sandbox.close()
        self._sandboxes.clear()
        self.sandbox = None
        if self._default_workspace_root is not None and self.store is not None:
            self.sandbox = self._sandbox_for(self._default_workspace_root)
            self.backend = self._build_backend(self._default_workspace_root, allow_execute=True)

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

        # Explicit off switch: keep whatever model was remembered, but run
        # the built-in default embedder.
        if config.get("enabled") is False:
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
        model: str | None,
        dimensions: int = 0,
    ) -> dict[str, int]:
        """Switch embedding model, rebuild Qdrant collections and re-index.

        ``model=None`` switches back to the built-in default embedder.
        Changing model usually changes vector dimensions, therefore old
        collections cannot be reused safely.
        """

        if self.sqlite is None:
            raise RuntimeError("MemoryProvider is not open")

        if not self._settings.memory.vector_store.enabled:
            raise RuntimeError("Vector store is disabled")

        if model:
            self.vector.configure_ollama(
                base_url=self.ollama_embedding_base_url,
                model=model,
                vector_size=dimensions,
            )
        else:
            self.vector.configure_placeholder()

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
        # Re-index durable episodes (collection was reset above)
        # -------------------------------------------------------------
        episodes = await self.sqlite.fetch(
            "SELECT id, summary, user_id, scope FROM episodes ORDER BY created_at ASC"
        )
        episode_count = 0
        for start in range(0, len(episodes), 32):
            episode_count += await asyncio.to_thread(
                self.vector.upsert_many,
                "episodes",
                [
                    {
                        "doc_id": str(item["id"]),
                        "text": str(item["summary"]),
                        "payload": {"user_id": item["user_id"], "scope": item["scope"]},
                    }
                    for item in episodes[start : start + 32]
                ],
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
            "episodes": episode_count,
            "attachment_chunks": attachment_chunk_count,
        }

    async def close(self) -> None:
        self.vector.close()
        # Every conversation's sandbox, not just the default folder's: a
        # workspace-specific sandbox outlives its run until shutdown.
        for sandbox in list(self._sandboxes.values()):
            sandbox.close()
        self._sandboxes.clear()
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

    def agent_kwargs(
        self,
        *,
        allow_execute: bool = True,
        workspace_root: str | None = None,
    ) -> dict[str, Any]:
        """Keyword arguments for `create_deep_agent(model, **agent_kwargs())`.

        With ``allow_execute=False`` (Terminal permission = DENY) the runtime
        backend is replaced by a StateBackend that has no ``execute`` tool, so
        Deep Agents does not surface local process execution to the model.

        ``workspace_root`` rebinds ``/workspace/`` for one conversation's run.
        Omitting it keeps the configured default, which is what every
        conversation without a pinned folder gets.
        """
        if (
            self.checkpointer is None
            or self.store is None
            or self.backend is None
            or self._default_workspace_root is None
        ):
            raise RuntimeError(
                "MemoryProvider is not open — call await provider.open() first"
            )

        backend = self._backend_for(workspace_root, allow_execute=allow_execute)

        return {
            "checkpointer": self.checkpointer,
            "store": self.store,
            "backend": backend,
            "memory": self.memory_files,
            "skills": [self.skills_path],
        }

    def _backend_for(
        self,
        workspace_root: str | None,
        *,
        allow_execute: bool,
    ) -> CompositeBackend:
        """Backend whose ``/workspace/`` route points at ``workspace_root``."""

        if (
            self._default_workspace_root is None
            or self._uploads_root is None
            or self.store is None
        ):
            raise RuntimeError(
                "MemoryProvider is not open — call await provider.open() first"
            )

        root = str(Path(workspace_root).resolve()) if workspace_root else self._default_workspace_root

        # The default folder's backend is built once at startup and reused so
        # the common case allocates nothing per run.
        if allow_execute and root == self._default_workspace_root and self.backend is not None:
            return self.backend

        return self._build_backend(root, allow_execute=allow_execute)

    def _sandbox_for(self, workspace_root: str) -> NativeWindowsSandboxBackend | None:
        """Secure Windows process sandbox per selected workspace; fail closed.

        A missing helper must never enable unrestricted host execution.
        The chat remains available without the execute tool and surfaces a
        descriptive error in logs/Settings until the native helper is installed.
        """
        if not self._settings.sandbox.enabled:
            return None
        existing = self._sandboxes.get(workspace_root)
        if existing is not None:
            return existing
        if self._uploads_root is None:
            return None
        try:
            sandbox = NativeWindowsSandboxBackend(
                workspace_root=workspace_root,
                uploads_root=str(self._uploads_root),
                timeout_seconds=self._settings.sandbox.timeout_seconds,
                memory_limit=self._settings.sandbox.memory_limit,
                cpu_limit=self._settings.sandbox.cpu_limit,
                network_enabled=self._settings.sandbox.network_enabled,
            )
        except (NativeSandboxUnavailable, OSError, ValueError) as exc:
            logger.error("Native execution disabled (fail closed): %s", exc)
            return None
        self._sandboxes[workspace_root] = sandbox
        return sandbox

    def _build_backend(self, workspace_root: str, *, allow_execute: bool) -> CompositeBackend:
        """Compose the route backends around one conversation's workspace."""

        if self.store is None or self._uploads_root is None:
            raise RuntimeError(
                "MemoryProvider is not open — call await provider.open() first"
            )

        ns_local = ("trajecta-local",)
        workspace_root = str(Path(workspace_root).resolve())

        # StateBackend has no ``execute`` capability, so Deep Agents does not
        # create the built-in ``execute`` tool when Terminal is DENY.
        default_backend: Any = StateBackend()
        if allow_execute:
            sandbox = self._sandbox_for(workspace_root)
            if sandbox is not None:
                default_backend = sandbox

        return CompositeBackend(
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
                    root_dir=workspace_root,
                    virtual_mode=True,
                ),
                "/uploads/": FilesystemBackend(
                    root_dir=str(self._uploads_root),
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