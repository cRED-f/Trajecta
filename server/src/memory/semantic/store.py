"""Semantic memory — durable facts usable across future tasks.

Durable cross-thread facts live as files under `/memories/` in the LangGraph
store (LongGraph SQLite). Trajecta mirrors those facts into its own SQLite+Qdrant
for lexical/semantic retrieval even when no graph is executing.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from deepagents.backends import StoreBackend
from deepagents.backends.protocol import ReadResult

if TYPE_CHECKING:
    from server.src.memory.provider import MemoryProvider

MEMORIES_DIR = "/memories/"
AGENTS_MD = MEMORIES_DIR + "AGENTS.md"


class SemanticMemory:
    """Durable cross-thread facts stored at `/memories/<key>` via the store backend.

    Every put is mirrored into FTS (+ Qdrant, when available). Reads and searches
    work offline against the local stores. Falls back to the store backend for
    the canonical copy (source of truth for the agent).
    """

    def __init__(self, provider: "MemoryProvider") -> None:
        self._provider = provider

    def _backend(self) -> StoreBackend:
        backend = self._provider.backend
        if backend is None:
            raise RuntimeError("MemoryProvider not open — call await provider.open() first")
        return backend.routes[MEMORIES_DIR]

    def _path(self, key: str) -> str:
        return MEMORIES_DIR + key.lstrip("/")

    @staticmethod
    def _memory_id(key: str) -> str:
        return hashlib.sha256(f"memories:{key}".encode()).hexdigest()

    # -- CRUD --------------------------------------------------------------

    async def aput(self, key: str, content: str) -> None:
        """Write a durable fact; mirror into FTS + Qdrant for retrieval."""
        await self._backend().awrite(self._path(key), content)
        await self._amirror(key, content)

    async def _amirror(self, key: str, content: str) -> None:
        memory_id = self._memory_id(key)
        if self._provider.fts is not None:
            row = await self._provider.fts.fetch_memory(memory_id)
            if row:
                await self._provider.fts.update(memory_id, content)
            else:
                await self._provider.fts.add(memory_id, "semantic", "memories", key, content)
        if self._provider.vector is not None:
            self._provider.vector.upsert("memories", memory_id, content)

    async def aget(self, key: str) -> str | None:
        """Content of a fact (None if missing)."""
        res: ReadResult = await self._backend().aread(self._path(key))
        if res.error or res.file_data is None:
            return None
        return res.file_data.get("content")

    async def adelete(self, key: str) -> None:
        """Remove a fact and all local mirrors."""
        memory_id = self._memory_id(key)

        await self._backend().adelete(self._path(key))

        if self._provider.fts is not None:
            await self._provider.fts.remove(memory_id)

        if self._provider.vector is not None:
            self._provider.vector.delete("memories", memory_id)

    # -- retrieval ---------------------------------------------------------

    async def asearch(self, query: str, limit: int = 10) -> list[dict]:
        """Lexical (FTS) results first, then Qdrant (deduped) when available."""
        result: list[dict] = []
        seen: set[str] = set()
        if self._provider.fts is not None:
            for r in await self._provider.fts.search(query, limit=limit):
                key = r.get("key") or ""
                if key:
                    seen.add(key)
                result.append(r)
        if self._provider.vector is not None and len(result) < limit:
            for r in self._provider.vector.search(query, limit=limit):
                if r.get("doc_id") not in seen:
                    result.append(r)
        return result[:limit]