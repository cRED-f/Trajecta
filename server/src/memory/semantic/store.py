"""Semantic memory — durable facts usable across future tasks.

Canonical facts live under /memories/ through Deep Agents CompositeBackend.

Trajecta additionally mirrors them into:
    - SQLite + FTS5 for lexical retrieval
    - Qdrant for semantic retrieval
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from deepagents.backends.protocol import BackendProtocol, ReadResult

if TYPE_CHECKING:
    from server.src.memory.provider import MemoryProvider


MEMORIES_DIR = "/memories/"
AGENTS_MD = MEMORIES_DIR + "AGENTS.md"


class SemanticMemory:
    """Durable semantic memory with hybrid local retrieval."""

    def __init__(self, provider: "MemoryProvider") -> None:
        self._provider = provider

    def _backend(self) -> BackendProtocol:
        """
        Use CompositeBackend rather than directly accessing its route.

        /memories/foo
               ↓ CompositeBackend
        /foo
               ↓ StoreBackend
        """

        backend = self._provider.backend

        if backend is None:
            raise RuntimeError(
                "MemoryProvider not open — call await provider.open() first"
            )

        return backend

    def _path(self, key: str) -> str:
        return MEMORIES_DIR + key.lstrip("/")

    @staticmethod
    def _memory_id(key: str) -> str:
        return hashlib.sha256(
            f"memories:{key}".encode()
        ).hexdigest()

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    async def aput(
        self,
        key: str,
        content: str,
    ) -> None:
        """Write canonical memory and synchronize retrieval indexes."""

        result = await self._backend().awrite(
            self._path(key),
            content,
        )

        if result.error:
            raise RuntimeError(
                f"Failed to write semantic memory {key!r}: "
                f"{result.error}"
            )

        await self._amirror(key, content)

    async def _amirror(
        self,
        key: str,
        content: str,
    ) -> None:
        """Synchronize Trajecta's retrieval indexes."""

        memory_id = self._memory_id(key)

        if self._provider.fts is not None:
            existing = await self._provider.fts.fetch_memory(
                memory_id
            )

            if existing:
                await self._provider.fts.update(
                    memory_id,
                    content,
                )
            else:
                await self._provider.fts.add(
                    memory_id,
                    "semantic",
                    "memories",
                    key,
                    content,
                )

        self._provider.vector.upsert(
            "memories",
            memory_id,
            content,
        )

    async def aget(
        self,
        key: str,
    ) -> str | None:
        """Load canonical semantic memory."""

        result: ReadResult = await self._backend().aread(
            self._path(key)
        )

        if result.error or result.file_data is None:
            return None

        return result.file_data.get("content")

    async def adelete(
        self,
        key: str,
    ) -> None:
        """Delete canonical memory and every retrieval mirror."""

        memory_id = self._memory_id(key)

        result = await self._backend().adelete(
            self._path(key)
        )

        if result.error:
            raise RuntimeError(
                f"Failed to delete semantic memory {key!r}: "
                f"{result.error}"
            )

        if self._provider.fts is not None:
            await self._provider.fts.remove(memory_id)

        self._provider.vector.delete(
            "memories",
            memory_id,
        )

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    async def asearch(
        self,
        query: str,
        limit: int = 10,
    ) -> list[dict]:
        """
        Hybrid retrieval.

        Current strategy:
            1. lexical FTS results
            2. Qdrant results not already returned by FTS

        Later this can become true score fusion / reranking.
        """

        limit = max(1, int(limit))

        results: list[dict] = []
        seen_doc_ids: set[str] = set()

        # --------------------------------------------------------------
        # FTS
        # --------------------------------------------------------------

        if self._provider.fts is not None:
            lexical_hits = await self._provider.fts.search(
                query,
                limit=limit,
                tier="semantic",
                namespace="memories",
            )

            for hit in lexical_hits:
                key = hit.get("key") or ""

                if not key:
                    continue

                doc_id = self._memory_id(key)

                if doc_id in seen_doc_ids:
                    continue

                seen_doc_ids.add(doc_id)

                results.append(
                    {
                        "source": "fts",
                        "doc_id": doc_id,
                        "tier": hit.get("tier"),
                        "namespace": hit.get("namespace"),
                        "key": key,
                        "content": hit.get("content", ""),
                        "snippet": hit.get("snippet"),
                    }
                )

        # --------------------------------------------------------------
        # Vector
        # --------------------------------------------------------------

        remaining = limit - len(results)

        if remaining > 0:
            vector_hits = self._provider.vector.search(
                "memories",
                query,
                limit=remaining,
            )

            for hit in vector_hits:
                doc_id = hit.get("doc_id")

                if not doc_id or doc_id in seen_doc_ids:
                    continue

                seen_doc_ids.add(doc_id)

                results.append(
                    {
                        "source": "vector",
                        "doc_id": doc_id,
                        "tier": "semantic",
                        "namespace": "memories",
                        "content": hit.get("text", ""),
                        "score": hit.get("score"),
                    }
                )

                if len(results) >= limit:
                    break

        return results[:limit]