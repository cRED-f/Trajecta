"""VectorStore — embedded-mode Qdrant for semantic retrieval.

In-process `QdrantClient(path=...)` — no separate server process. Collections per
doc type: `trajecta_memories`, `trajecta_episodes`. Embeddings come from a
configurable callable; the default is SHA1-hash bag-of-words buckets (deterministic,
offline, no model), swapped for a real embedder when skills/eval land. Qdrant
unavailability degrades gracefully: callers keep working via FTS/DB.
"""

from __future__ import annotations

import hashlib
import math
from collections import Counter
from typing import Any, Callable

try:  # qdrant-client is optional until the vector store is actually used
    from qdrant_client import QdrantClient
    from qdrant_client.http import models as qm

    QDRANT_AVAILABLE = True
except Exception:  # pragma: no cover  RuntimeError if libtorch/msvc missing
    QDRANT_AVAILABLE = False

EMBED_DIM = 64


def _tokens(text: str) -> list[str]:
    import re

    return [t.lower() for t in re.findall(r"[a-z0-9]+", text, re.I)]


def default_embedder(text: str) -> list[float]:
    """Deterministic offline embedding: token → hash bucket, normalized TF-style."""
    counter = Counter(_tokens(text))
    total = sum(counter.values())
    vec = [0.0] * EMBED_DIM
    for tok, count in counter.items():
        if not total:
            break
        bucket = int(hashlib.sha1(tok.encode()).hexdigest(), 16) % EMBED_DIM
        vec[bucket] = math.sqrt(count / total)
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


class VectorStore:
    RESILIENT_BROKEN = False  # class latch: if Qdrant can't init, keep ourselves a no-op

    def __init__(
        self,
        path: str,
        embedder: Callable[[str], list[float]] | None = None,
        collection_prefix: str = "trajecta_",
    ) -> None:
        self._path = path
        self._prefix = collection_prefix
        self._embedder: Callable[[str], list[float]] = embedder or default_embedder
        self._client: Any | None = None

    # -- lifecycle ---------------------------------------------------------

    def open(self) -> None:
        if not QDRANT_AVAILABLE or VectorStore.RESILIENT_BROKEN:
            VectorStore.RESILIENT_BROKEN = True
            return
        try:
            self._client = QdrantClient(path=self._path)
            self._ensure_collection("memories")
            self._ensure_collection("episodes")
        except Exception:  # pragma: no cover
            VectorStore.RESILIENT_BROKEN = True
            self._client = None

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception:  # pragma: no cover
                pass
            self._client = None

    def _ensure_collection(self, name: str) -> None:
        assert self._client is not None
        full = f"{self._prefix}{name}"
        if not self._client.collection_exists(full):
            self._client.create_collection(
                collection_name=full,
                vectors_config=qm.VectorParams(size=EMBED_DIM, distance=qm.Distance.COSINE),
            )

    # -- data --------------------------------------------------------------

    def upsert(self, namespace: str, doc_id: str, text: str) -> None:
        """Embed `text` and store point (namespace, doc_id → payload)."""
        if self._client is None:
            return
        vector = self._embedder(text)
        try:
            self._client.upsert(
                collection_name=self._collection_for(namespace),
                points=[
                    qm.PointStruct(
                        id=_hash_id(namespace, doc_id), vector=vector,
                        payload={"namespace": namespace, "doc_id": doc_id, "text": text},
                    )
                ],
            )
        except Exception:  # pragma: no cover
            pass

    def delete(self, namespace: str, doc_id: str) -> None:
        """Remove a single point (hashed id) from a collection."""
        if self._client is None:
            return
        try:
            self._client.delete(
                collection_name=self._collection_for(namespace),
                points_selector=[_hash_id(namespace, doc_id)],
            )
        except Exception:  # pragma: no cover
            pass

    def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        """Semantic search over recently-upserted documents."""
        if self._client is None:
            return []
        results: list[dict[str, Any]] = []
        for namespace in ("memories", "episodes"):
            hits = self._search_collection(self._collection_for(namespace), query, limit)
            results.extend(hits)
        results.sort(key=lambda r: r["score"], reverse=True)
        return results[:limit]

    def _search_collection(self, full: str, query: str, limit: int) -> list[dict[str, Any]]:
        if not self._client.collection_exists(full):
            return []
        try:
            hits = self._client.search(
                collection_name=full,
                query_vector=self._embedder(query),
                limit=limit,
                with_payload=True,
            )
        except Exception:  # pragma: no cover
            return []
        return [
            {
                "score": float(hit.score) if hit.score is not None else 0.0,
                "namespace": hit.payload.get("namespace"),
                "doc_id": hit.payload.get("doc_id"),
                "text": hit.payload.get("text", ""),
            }
            for hit in hits
        ]

    def _collection_for(self, namespace: str) -> str:
        return f"{self._prefix}{namespace}"


def _hash_id(namespace: str, doc_id: str) -> int:
    """Deterministic positive int point id for Qdrant."""
    return int(hashlib.sha256(f"{namespace}:{doc_id}".encode()).hexdigest(), 16) % (1 << 63)