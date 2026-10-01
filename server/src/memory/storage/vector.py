"""Embedded Qdrant vector retrieval.

Local-first:
    - no external Qdrant server required
    - gracefully degrades if Qdrant is unavailable
    - errors are logged instead of silently disappearing

The default embedding implementation is intentionally only an offline
development placeholder. Replace it with a real embedding model later.
"""

from __future__ import annotations

import hashlib
import logging
import math
import re

from collections import Counter
from typing import Any, Callable

from server.src.memory.embeddings import OllamaEmbedder


logger = logging.getLogger(__name__)


try:
    from qdrant_client import QdrantClient
    from qdrant_client.http import models as qm

    QDRANT_AVAILABLE = True

except Exception:
    QdrantClient = None  # type: ignore[assignment]
    qm = None  # type: ignore[assignment]
    QDRANT_AVAILABLE = False


EMBED_DIM = 64


def _tokens(text: str) -> list[str]:
    return [
        token.lower()
        for token in re.findall(
            r"[a-z0-9]+",
            text,
            re.I,
        )
    ]


def default_embedder(
    text: str,
) -> list[float]:
    """
    Deterministic offline placeholder embedding.

    This is NOT intended as the final semantic embedding model.
    """

    counter = Counter(_tokens(text))
    total = sum(counter.values())

    vector = [0.0] * EMBED_DIM

    if total == 0:
        return vector

    for token, count in counter.items():
        bucket = (
            int(
                hashlib.sha1(token.encode()).hexdigest(),
                16,
            )
            % EMBED_DIM
        )

        vector[bucket] += math.sqrt(
            count / total
        )

    norm = (
        math.sqrt(
            sum(value * value for value in vector)
        )
        or 1.0
    )

    return [
        value / norm
        for value in vector
    ]


class VectorStore:
    """Embedded Qdrant wrapper."""

    def __init__(
        self,
        path: str,
        embedder: Callable[[str], list[float]] | None = None,
        collection_prefix: str = "trajecta_",
        vector_size: int = EMBED_DIM,
    ) -> None:
        self._path = path
        self._prefix = collection_prefix
        self._embedder = embedder or default_embedder
        self._vector_size = vector_size
        self._embedding_provider = "placeholder"
        self._embedding_model: str | None = None

        self._client: Any | None = None

    # ------------------------------------------------------------------
    # Embedding configuration
    # ------------------------------------------------------------------

    @property
    def vector_size(self) -> int:
        return self._vector_size

    @property
    def embedding_provider(self) -> str:
        return self._embedding_provider

    @property
    def embedding_model(self) -> str | None:
        return self._embedding_model

    def configure_ollama(
        self,
        *,
        base_url: str,
        model: str,
        vector_size: int,
    ) -> None:
        """Switch to a local Ollama embedding model (call before open())."""

        if vector_size <= 0:
            raise ValueError("vector_size must be positive")

        self._close_embedder()

        self._embedder = OllamaEmbedder(
            base_url=base_url,
            model=model,
        )
        self._vector_size = int(vector_size)
        self._embedding_provider = "ollama"
        self._embedding_model = model

    def configure_placeholder(self) -> None:
        """Fall back to the offline development placeholder embedder."""

        self._close_embedder()

        self._embedder = default_embedder
        self._vector_size = EMBED_DIM
        self._embedding_provider = "placeholder"
        self._embedding_model = None

    def _close_embedder(self) -> None:
        close = getattr(self._embedder, "close", None)
        if not callable(close):
            return

        try:
            close()
        except Exception:
            logger.exception("Failed to close embedding client")

    def reset_collections(self, namespaces: list[str]) -> None:
        """Drop and recreate collections (used when vector dimensions change)."""

        if self._client is None:
            raise RuntimeError("Qdrant vector store is not open")

        for namespace in namespaces:
            name = self._collection_for(namespace)

            if self._client.collection_exists(name):
                self._client.delete_collection(collection_name=name)

            self._ensure_collection(namespace)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def open(self) -> None:
        if not QDRANT_AVAILABLE:
            logger.warning(
                "qdrant-client is unavailable; "
                "semantic vector retrieval is disabled"
            )
            return

        try:
            self._client = QdrantClient(
                path=self._path
            )

            self._ensure_collection(
                "memories"
            )

            self._ensure_collection(
                "episodes"
            )

        except Exception:
            logger.exception(
                "Failed to initialize embedded Qdrant at %s",
                self._path,
            )

            self._client = None

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.close()

            except Exception:
                logger.exception(
                    "Failed to close embedded Qdrant"
                )

            finally:
                self._client = None

        self._close_embedder()

    def _ensure_collection(
        self,
        namespace: str,
    ) -> None:
        if self._client is None:
            return

        name = self._collection_for(
            namespace
        )

        if self._client.collection_exists(name):
            return

        self._client.create_collection(
            collection_name=name,
            vectors_config=qm.VectorParams(
                size=self._vector_size,
                distance=qm.Distance.COSINE,
            ),
        )

    # ------------------------------------------------------------------
    # Data
    # ------------------------------------------------------------------

    def upsert(
        self,
        namespace: str,
        doc_id: str,
        text: str,
        *,
        payload: dict[str, Any] | None = None,
    ) -> None:
        if self._client is None:
            return

        try:
            self._ensure_collection(namespace)
            vector = self._embedder(text)
            final_payload = {
                "namespace": namespace,
                "doc_id": doc_id,
                "text": text,
            }
            if payload:
                final_payload.update(payload)

            self._client.upsert(
                collection_name=self._collection_for(
                    namespace
                ),
                points=[
                    qm.PointStruct(
                        id=_hash_id(
                            namespace,
                            doc_id,
                        ),
                        vector=vector,
                        payload=final_payload,
                    )
                ],
            )

        except Exception:
            logger.exception(
                "Qdrant upsert failed: "
                "namespace=%s doc_id=%s",
                namespace,
                doc_id,
            )

    def upsert_many(
        self,
        namespace: str,
        items: list[dict[str, Any]],
    ) -> int:
        """Embed and upsert a batch of documents with a single embed call."""

        if self._client is None or not items:
            return 0

        try:
            self._ensure_collection(namespace)

            texts = [str(item.get("text") or "") for item in items]

            embed_many = getattr(self._embedder, "embed_many", None)
            if callable(embed_many):
                vectors = embed_many(texts)
            else:
                vectors = [self._embedder(text) for text in texts]

            if len(vectors) != len(items):
                raise RuntimeError("embedding batch size mismatch")

            points = []
            for item, vector in zip(items, vectors, strict=True):
                doc_id = str(item["doc_id"])

                final_payload = {
                    "namespace": namespace,
                    "doc_id": doc_id,
                    "text": str(item.get("text") or ""),
                }

                payload = item.get("payload")
                if isinstance(payload, dict):
                    final_payload.update(payload)

                points.append(
                    qm.PointStruct(
                        id=_hash_id(namespace, doc_id),
                        vector=vector,
                        payload=final_payload,
                    )
                )

            self._client.upsert(
                collection_name=self._collection_for(namespace),
                points=points,
            )

            return len(points)

        except Exception:
            logger.exception(
                "Qdrant batch upsert failed: namespace=%s count=%s",
                namespace,
                len(items),
            )
            return 0

    def delete(
        self,
        namespace: str,
        doc_id: str,
    ) -> None:
        if self._client is None:
            return

        try:
            self._client.delete(
                collection_name=self._collection_for(
                    namespace
                ),
                points_selector=[
                    _hash_id(
                        namespace,
                        doc_id,
                    )
                ],
            )

        except Exception:
            logger.exception(
                "Qdrant delete failed: "
                "namespace=%s doc_id=%s",
                namespace,
                doc_id,
            )

    def search(
        self,
        namespace: str,
        query: str,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """
        Search ONE logical namespace.

        Semantic memory should not accidentally retrieve episodic documents,
        and vice versa.
        """

        if self._client is None:
            return []

        limit = max(1, int(limit))
        self._ensure_collection(namespace)

        return self._search_collection(
            self._collection_for(namespace),
            query,
            limit,
        )

    def _search_collection(
        self,
        collection_name: str,
        query: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        if self._client is None:
            return []

        try:
            if not self._client.collection_exists(collection_name):
                return []

            response = self._client.query_points(
                collection_name=collection_name,
                query=self._embedder(query),
                limit=limit,
                with_payload=True,
            )

            hits = response.points

        except Exception:
            logger.exception(
                "Qdrant search failed: collection=%s",
                collection_name,
            )
            return []

        return [
            {
                "score": (
                    float(hit.score)
                    if hit.score is not None
                    else 0.0
                ),
                "namespace": (
                    hit.payload or {}
                ).get("namespace"),
                "doc_id": (
                    hit.payload or {}
                ).get("doc_id"),
                "text": (
                    hit.payload or {}
                ).get("text", ""),
                "payload": dict(hit.payload or {}),
            }
            for hit in hits
        ]
    def _collection_for(
        self,
        namespace: str,
    ) -> str:
        return f"{self._prefix}{namespace}"


def _hash_id(
    namespace: str,
    doc_id: str,
) -> int:
    """Deterministic positive 63-bit Qdrant point id."""

    digest = hashlib.sha256(
        f"{namespace}:{doc_id}".encode()
    ).digest()

    return (
        int.from_bytes(
            digest[:8],
            "big",
        )
        & 0x7FFFFFFFFFFFFFFF
    )