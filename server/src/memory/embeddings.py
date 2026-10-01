"""Ollama embedding discovery and local embedding client.

Discovery:
    GET  /api/tags   → installed models
    POST /api/show   → capabilities (only `embedding` models are exposed)

Embedding:
    POST /api/embed  → single string or list of strings → list of vectors
"""

from __future__ import annotations

import asyncio

from dataclasses import dataclass
from typing import Any

import httpx


DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"


@dataclass(slots=True, frozen=True)
class OllamaEmbeddingModel:
    name: str
    size: int
    modified_at: str | None
    parameter_size: str | None
    quantization_level: str | None
    family: str | None
    capabilities: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "size": self.size,
            "modified_at": self.modified_at,
            "parameter_size": self.parameter_size,
            "quantization_level": self.quantization_level,
            "family": self.family,
            "capabilities": list(self.capabilities),
        }


class OllamaEmbedder:
    """Synchronous embedding callable used by VectorStore."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        timeout_seconds: float = 60.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._client = httpx.Client(
            timeout=timeout_seconds,
            transport=transport,
        )

    def __call__(self, text: str) -> list[float]:
        return self.embed_many([text])[0]

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        response = self._client.post(
            f"{self.base_url}/api/embed",
            json={
                "model": self.model,
                "input": texts,
            },
        )
        response.raise_for_status()

        payload = response.json()
        embeddings = (
            payload.get("embeddings")
            if isinstance(payload, dict)
            else None
        )

        if (
            not isinstance(embeddings, list)
            or len(embeddings) != len(texts)
        ):
            raise RuntimeError(
                f"Ollama model {self.model!r} returned an invalid embedding batch"
            )

        result: list[list[float]] = []
        for vector in embeddings:
            if not isinstance(vector, list) or not vector:
                raise RuntimeError(
                    f"Ollama model {self.model!r} returned an invalid embedding vector"
                )
            result.append([float(value) for value in vector])

        return result

    def close(self) -> None:
        self._client.close()


class OllamaEmbeddingCatalogService:
    """Discover embedding-capable models installed in local Ollama."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = 5.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = (base_url or DEFAULT_OLLAMA_URL).rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._transport = transport

    async def list_models(
        self,
    ) -> tuple[bool, list[OllamaEmbeddingModel], str | None]:
        """Return (reachable, embedding-capable models, error)."""

        semaphore = asyncio.Semaphore(6)

        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds,
                transport=self._transport,
            ) as client:
                response = await client.get(f"{self.base_url}/api/tags")
                response.raise_for_status()

                payload = response.json()
                raw_models = (
                    payload.get("models", [])
                    if isinstance(payload, dict)
                    else []
                )

                if not isinstance(raw_models, list):
                    return True, [], None

                async def inspect(item: Any) -> OllamaEmbeddingModel | None:
                    if not isinstance(item, dict):
                        return None

                    name = str(
                        item.get("name") or item.get("model") or ""
                    ).strip()
                    if not name:
                        return None

                    try:
                        async with semaphore:
                            detail_response = await client.post(
                                f"{self.base_url}/api/show",
                                json={"model": name, "verbose": False},
                            )
                            detail_response.raise_for_status()
                            detail = detail_response.json()
                    except (httpx.HTTPError, ValueError):
                        return None

                    capabilities = tuple(
                        str(value)
                        for value in (
                            detail.get("capabilities", [])
                            if isinstance(detail, dict)
                            else []
                        )
                    )

                    # Only expose real embedding-capable models.
                    if "embedding" not in capabilities:
                        return None

                    details = (
                        detail.get("details", {})
                        if isinstance(detail, dict)
                        else {}
                    )
                    if not isinstance(details, dict):
                        details = {}

                    return OllamaEmbeddingModel(
                        name=name,
                        size=int(item.get("size") or 0),
                        modified_at=(
                            str(item["modified_at"])
                            if item.get("modified_at")
                            else None
                        ),
                        parameter_size=(
                            str(details["parameter_size"])
                            if details.get("parameter_size")
                            else None
                        ),
                        quantization_level=(
                            str(details["quantization_level"])
                            if details.get("quantization_level")
                            else None
                        ),
                        family=(
                            str(details["family"])
                            if details.get("family")
                            else None
                        ),
                        capabilities=capabilities,
                    )

                inspected = await asyncio.gather(
                    *(inspect(item) for item in raw_models)
                )

        except (httpx.HTTPError, ValueError) as exc:
            return False, [], str(exc)

        models = sorted(
            (model for model in inspected if model is not None),
            key=lambda model: model.name.lower(),
        )

        return True, models, None

    async def probe(self, model: str) -> int:
        """Validate the model and return its embedding vector dimension."""

        model = model.strip()
        if not model:
            raise ValueError("embedding model is required")

        reachable, models, error = await self.list_models()

        if not reachable:
            raise RuntimeError(
                f"Ollama is not reachable at {self.base_url}: "
                f"{error or 'unknown error'}"
            )

        installed = {item.name for item in models}
        if model not in installed:
            raise ValueError(
                f"Ollama model {model!r} is not installed or does not "
                "support embeddings"
            )

        try:
            async with httpx.AsyncClient(
                timeout=max(self.timeout_seconds, 60.0),
                transport=self._transport,
            ) as client:
                response = await client.post(
                    f"{self.base_url}/api/embed",
                    json={
                        "model": model,
                        "input": "Trajecta embedding model validation",
                    },
                )
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise RuntimeError(
                f"Failed to generate a test embedding with {model!r}: {exc}"
            ) from exc

        embeddings = (
            payload.get("embeddings")
            if isinstance(payload, dict)
            else None
        )

        if not isinstance(embeddings, list) or not embeddings:
            raise RuntimeError(f"Ollama model {model!r} returned no embedding")

        vector = embeddings[0]
        if not isinstance(vector, list) or not vector:
            raise RuntimeError(
                f"Ollama model {model!r} returned an invalid embedding"
            )

        return len(vector)
