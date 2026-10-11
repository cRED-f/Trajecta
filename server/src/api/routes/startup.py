"""Cold-start model warmup for the locally managed desktop application.

Never send a speculative request to a paid model. Only models explicitly
selected from a local Ollama provider are warmed; the existing FastAPI lifespan
already restores stored settings and starts the durable learning worker.
"""
from __future__ import annotations

import asyncio
from typing import Any

import httpx
from fastapi import APIRouter, Request

router = APIRouter(prefix="/startup", tags=["startup"])


async def _warm_local_model(
    *, base_url: str, model: str, embedding: bool,
) -> dict[str, Any]:
    endpoint = "/api/embed" if embedding else "/api/generate"
    body: dict[str, Any] = {"model": model, "keep_alive": "5m"}
    if embedding:
        # Validates that the embedding model actually produces embeddings.
        body["input"] = "Trajecta startup"
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(18.0)) as client:
            response = await client.post(f"{base_url.rstrip('/')}{endpoint}", json=body)
            response.raise_for_status()
            result = response.json()
            if embedding and not (isinstance(result, dict) and result.get("embeddings")):
                return {"state": "unavailable", "model": model, "reason": "Embedding response was empty"}
        return {"state": "ready", "model": model}
    except (httpx.HTTPError, ValueError) as exc:
        # Model or Ollama availability does not impact chat/SQLite readiness.
        return {"state": "unavailable", "model": model, "reason": str(exc)[:200]}


@router.post("/warmup")
async def warmup_selected_models(request: Request) -> dict[str, dict[str, Any]]:
    """Warm configured local models without loading any unselected model.

    The learning worker runs independently of warmup. Remote/Bifrost models
    are not prompted or billed during application startup.
    """
    memory = request.app.state.memory_provider
    worker = request.app.state.reflection_worker
    result: dict[str, dict[str, Any]] = {
        "embedding": {"state": "skipped", "reason": "No Ollama embedding selected"},
        "learning": {"state": "skipped", "reason": "No local learning model selected"},
    }
    jobs: dict[str, Any] = {}
    url = memory.ollama_embedding_base_url

    if memory.vector.embedding_provider == "ollama" and memory.vector.embedding_model:
        jobs["embedding"] = _warm_local_model(
            base_url=url, model=memory.vector.embedding_model, embedding=True,
        )

    config = await worker.get_config()
    if config.get("enabled"):
        model = config.get("model")
        if not model:
            model = (await request.app.state.llm_settings.get()).get("default_model")
        if isinstance(model, str) and model.startswith("ollama/"):
            name = model.split("/", 1)[1].strip()
            if name:
                jobs["learning"] = _warm_local_model(
                    base_url=url, model=name, embedding=False,
                )
        else:
            result["learning"] = {
                "state": "configured", "model": model,
                "reason": "Bifrost-managed model: no inference request made at startup",
            }

    if jobs:
        keys = list(jobs)
        values = await asyncio.gather(*(jobs[key] for key in keys))
        result.update(zip(keys, values))
    return result
