"""Memory route handlers.

- GET   /api/v1/memory                  Settings/listing catalog (settings, counts, items)
- GET   /api/v1/memory/semantic/{key}   Fetch one semantic memory by key
- PATCH /api/v1/memory/settings         Toggle automatic memory
- GET   /api/v1/memory/embedding        Ollama embedding catalog + active model
- PATCH /api/v1/memory/embedding        Turn embedding on/off, switch model, re-index
- GET   /api/v1/memory/episodic/search  Hybrid episode retrieval
- GET   /api/v1/memory/episodic/{id}    Evidence-linked episode detail
- DELETE /api/v1/memory/episodic/{id}   Delete an episode and its indexes
- GET   /api/v1/memory/{type}           List memory entries by type
- POST  /api/v1/memory/{type}           Upsert a semantic memory entry (key + content)
- DELETE /api/v1/memory/{type}/{key}    Delete a semantic memory entry
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from server.src.memory.embeddings import OllamaEmbeddingCatalogService

router = APIRouter(prefix="/memory", tags=["memory"])

_KNOWN_TIERS = {"semantic", "episodic", "procedural"}


class MemoryWrite(BaseModel):
    key: str = Field(..., min_length=1, max_length=250)
    content: str = Field(..., min_length=1, max_length=100_000)


class MemorySettingsUpdate(BaseModel):
    automatic_memory: bool


class EmbeddingSettingsUpdate(BaseModel):
    enabled: bool | None = None
    model: str | None = Field(default=None, min_length=1, max_length=250)


def _provider(request: Request):
    return request.app.state.memory_provider


def _policy(request: Request):
    return request.app.state.permission_policy


async def _embedding_config(request: Request) -> dict[str, Any]:
    """Persisted ``memory.embedding`` row (empty when never configured)."""

    stored = await _policy(request).get_setting("memory.embedding", {})

    return stored if isinstance(stored, dict) else {}


@router.get("")
async def list_memory(
    request: Request,
    query: str = Query(default=""),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict:
    """Settings/UI listing of semantic memories + automatic-memory setting."""
    memory = _provider(request)

    if memory is None or memory.semantic is None:
        raise HTTPException(status_code=503, detail="Memory store is not ready")

    semantic = await memory.semantic.alist(query=query, limit=limit)
    episodic_count = await memory.sqlite.fetchone(
        "SELECT COUNT(*) AS count FROM episodes WHERE user_id = ?", ("local",)
    )
    procedural_names = await memory.procedural.alist()

    automatic_memory = await _policy(request).get_setting("automatic_memory", True)

    return {
        "settings": {"automatic_memory": bool(automatic_memory)},
        "counts": {
            "semantic": len(semantic),
            "episodic": int((episodic_count or {}).get("count") or 0),
            "procedural": len(procedural_names),
        },
        "items": semantic,
    }


@router.patch("/settings")
async def update_memory_settings(
    body: MemorySettingsUpdate,
    request: Request,
) -> dict:
    await _policy(request).set_setting("automatic_memory", body.automatic_memory)

    return {"automatic_memory": body.automatic_memory}


# ---------------------------------------------------------------------------
# Embedding model configuration
# ---------------------------------------------------------------------------


@router.get("/embedding")
async def embedding_settings(request: Request) -> dict[str, Any]:
    """Active embedding model plus the embedding-capable Ollama models installed."""

    memory = _provider(request)

    if memory is None:
        raise HTTPException(status_code=503, detail="Memory store is not ready")

    catalog = OllamaEmbeddingCatalogService(memory.ollama_embedding_base_url)
    reachable, models, error = await catalog.list_models()

    config = await _embedding_config(request)
    remembered_model = str(config.get("model") or "").strip() or None

    enabled = config.get("enabled")
    if not isinstance(enabled, bool):
        # Rows written before the switch existed: on iff a model is live.
        enabled = memory.vector.embedding_provider == "ollama"

    return {
        "vector_store_enabled": bool(
            request.app.state.settings.memory.vector_store.enabled
        ),
        "enabled": enabled,
        "provider": memory.vector.embedding_provider,
        # Live model while running, otherwise the remembered one, so turning
        # the switch back on restores the previous choice.
        "selected_model": memory.vector.embedding_model or remembered_model,
        "dimensions": memory.vector.vector_size,
        "ollama": {
            "base_url": memory.ollama_embedding_base_url,
            "reachable": reachable,
            "error": error,
        },
        "models": [model.as_dict() for model in models],
    }


@router.patch("/embedding")
async def update_embedding_settings(
    body: EmbeddingSettingsUpdate,
    request: Request,
) -> dict[str, Any]:
    """Turn embedding on/off, probe a model, swap the embedder and re-index."""

    memory = _provider(request)

    if memory is None or memory.sqlite is None:
        raise HTTPException(status_code=503, detail="Memory store is not ready")

    if not request.app.state.settings.memory.vector_store.enabled:
        raise HTTPException(status_code=409, detail="Vector store is disabled")

    config = await _embedding_config(request)
    remembered_model = str(config.get("model") or "").strip() or None
    remembered_dimensions = int(config.get("dimensions") or 0)

    enabled = True if body.enabled is None else bool(body.enabled)
    model = (body.model or "").strip() or (remembered_model if enabled else None)

    if not enabled:
        # Off: fall back to the built-in default embedder, keeping the last
        # model around so switching back on restores it in one step.
        if memory.vector.embedding_provider == "placeholder":
            reindexed = {"memories": 0, "attachment_chunks": 0}
        else:
            try:
                reindexed = await memory.reconfigure_embedding(model=None)
            except RuntimeError as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc

        await _policy(request).set_setting(
            "memory.embedding",
            {
                "enabled": False,
                "provider": "placeholder",
                "model": remembered_model,
                "dimensions": remembered_dimensions,
            },
        )

        return {
            "enabled": False,
            "provider": "placeholder",
            "selected_model": remembered_model,
            "dimensions": memory.vector.vector_size,
            "reindexed": reindexed,
        }

    if model is None:
        # On but nothing chosen yet: the default embedder stays live until a
        # model is picked.
        await _policy(request).set_setting(
            "memory.embedding",
            {
                "enabled": True,
                "provider": "placeholder",
                "model": None,
                "dimensions": remembered_dimensions,
            },
        )

        return {
            "enabled": True,
            "provider": memory.vector.embedding_provider,
            "selected_model": None,
            "dimensions": memory.vector.vector_size,
            "reindexed": {"memories": 0, "attachment_chunks": 0},
        }

    catalog = OllamaEmbeddingCatalogService(memory.ollama_embedding_base_url)

    try:
        dimensions = await catalog.probe(model)
        reindexed = await memory.reconfigure_embedding(
            model=model,
            dimensions=dimensions,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    # Persist only after a successful switch, so a failed probe leaves the
    # previously configured model in place for the next startup.
    await _policy(request).set_setting(
        "memory.embedding",
        {
            "enabled": True,
            "provider": "ollama",
            "model": model,
            "dimensions": dimensions,
        },
    )

    return {
        "enabled": True,
        "provider": "ollama",
        "selected_model": model,
        "dimensions": dimensions,
        "reindexed": reindexed,
    }


@router.get("/semantic/{key:path}")
async def get_semantic_memory(
    key: str,
    request: Request,
) -> dict:
    memory = _provider(request)

    if memory is None or memory.semantic is None:
        raise HTTPException(status_code=503, detail="Memory store is not ready")

    content = await memory.semantic.aget(key)

    if content is None:
        raise HTTPException(status_code=404, detail="Memory not found")

    return {"key": key, "content": content, "tier": "semantic"}


@router.get("/episodic/search")
async def search_episodes(
    request: Request,
    query: str = Query(..., min_length=1, max_length=2000),
    limit: int = Query(default=10, ge=1, le=100),
    scope: str = Query(default="local", max_length=64),
) -> list[dict[str, Any]]:
    """Search episodic memories without exposing other users' records."""
    memory = _provider(request)
    if memory is None or memory.sqlite is None:
        raise HTTPException(status_code=503, detail="Memory store is not ready")
    return await memory.episodic.search(query, limit, user_id="local", scope=scope)


@router.get("/episodic/{episode_id}")
async def get_episode(
    episode_id: str,
    request: Request,
    scope: str = Query(default="local", max_length=64),
) -> dict[str, Any]:
    memory = _provider(request)
    if memory is None or memory.sqlite is None:
        raise HTTPException(status_code=503, detail="Memory store is not ready")
    episode = await memory.episodic.get(episode_id, user_id="local", scope=scope)
    if episode is None:
        raise HTTPException(status_code=404, detail="Episode not found")
    return episode


@router.delete("/episodic/{episode_id}")
async def delete_episode(
    episode_id: str,
    request: Request,
    scope: str = Query(default="local", max_length=64),
) -> dict[str, bool]:
    memory = _provider(request)
    if memory is None or memory.sqlite is None:
        raise HTTPException(status_code=503, detail="Memory store is not ready")
    removed = await memory.episodic.delete(episode_id, user_id="local", scope=scope)
    if not removed:
        raise HTTPException(status_code=404, detail="Episode not found")
    return {"ok": True}


class ConflictResolution(BaseModel):
    preferred_ref: str = Field(min_length=3, max_length=200)


@router.get("/unified/search")
async def unified_search(request: Request, query: str = Query(min_length=1, max_length=1000),
                         workspace_path: str | None = None, limit: int = Query(8, ge=1, le=16)):
    """Inspection only; does not record retrieval as actual agent usage."""
    retriever = getattr(request.app.state, "memory_retriever", None)
    if retriever is None:
        raise HTTPException(503, "Unified retrieval is unavailable")
    return {"items": await retriever.search(query, workspace_path=workspace_path, limit=limit)}


@router.get("/unified/conflicts")
async def memory_conflicts(request: Request, status: str = Query("open", pattern="^(open|resolved)$"),
                           limit: int = Query(100, ge=1, le=200)):
    db = _provider(request).sqlite
    return {"items": await db.fetch(
        "SELECT * FROM memory_conflicts WHERE status=? ORDER BY created_at DESC LIMIT ?",
        (status, limit))}


@router.post("/unified/conflicts/{conflict_id}/resolve")
async def resolve_memory_conflict(conflict_id: int, body: ConflictResolution, request: Request):
    db = _provider(request).sqlite
    conflict = await db.fetchone("SELECT * FROM memory_conflicts WHERE id=?", (conflict_id,))
    if not conflict:
        raise HTTPException(404, "Conflict not found")
    if body.preferred_ref not in {conflict["left_ref"], conflict["right_ref"]}:
        raise HTTPException(400, "Choose one of the conflicting source references")
    from datetime import UTC, datetime
    await db.execute(
        """UPDATE memory_conflicts SET status='resolved', preferred_ref=?, resolved_at=?
           WHERE id=?""", (body.preferred_ref, datetime.now(UTC).isoformat(), conflict_id))
    return {"ok": True}


@router.get("/curator/findings")
async def curator_findings(request: Request, workspace_path: str | None = None,
                           limit: int = Query(100, ge=1, le=200)):
    from server.src.memory.episodic.store import EpisodicMemory
    scope = EpisodicMemory.workspace_scope(workspace_path)
    return {"items": await request.app.state.memory_curator.list(scope=scope, limit=limit)}


@router.post("/curator/scan")
async def curator_scan(request: Request, workspace_path: str | None = None,
                       stale_days: int = Query(30, ge=7, le=365)):
    from server.src.memory.episodic.store import EpisodicMemory
    scope = EpisodicMemory.workspace_scope(workspace_path)
    return {"items": await request.app.state.memory_curator.scan(
        scope=scope, stale_days=stale_days)}


@router.post("/curator/findings/{finding_id}/dismiss")
async def curator_dismiss(finding_id: int, request: Request,
                          workspace_path: str | None = None):
    from server.src.memory.episodic.store import EpisodicMemory
    if not await request.app.state.memory_curator.dismiss(
        finding_id, scope=EpisodicMemory.workspace_scope(workspace_path)):
        raise HTTPException(404, "Open finding not found")
    return {"ok": True}


@router.post("/curator/procedures/{draft_id}/archive")
async def curator_archive(draft_id: str, request: Request, workspace_path: str | None = None):
    from server.src.memory.episodic.store import EpisodicMemory
    if not await request.app.state.memory_curator.archive_proposal(
        draft_id, scope=EpisodicMemory.workspace_scope(workspace_path)):
        raise HTTPException(409, "Only pending proposals can be archived")
    return {"ok": True}


@router.post("/curator/procedures/{draft_id}/restore")
async def curator_restore(draft_id: str, request: Request, workspace_path: str | None = None):
    from server.src.memory.episodic.store import EpisodicMemory
    if not await request.app.state.memory_curator.restore_proposal(
        draft_id, scope=EpisodicMemory.workspace_scope(workspace_path)):
        raise HTTPException(409, "Archived proposal not found")
    return {"ok": True}


@router.get("/{memory_type}")
async def list_memories(
    memory_type: str,
    request: Request,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Return the saved memories of a given tier, newest first."""

    if memory_type not in _KNOWN_TIERS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown memory type {memory_type!r}",
        )

    provider = request.app.state.memory_provider

    if provider is None or provider.sqlite is None:
        raise HTTPException(status_code=503, detail="Memory store is not ready")

    if memory_type == "episodic":
        return await provider.episodic.list(limit=limit, user_id="local")

    if provider.fts is None:
        raise HTTPException(status_code=503, detail="Memory index is not ready")
    rows = await provider.fts._db.fetch(
        """
        SELECT id, tier, namespace, key, content, created_at, updated_at
        FROM memories
        WHERE tier = ?
        ORDER BY updated_at DESC
        LIMIT ?
        """,
        (memory_type, max(1, min(int(limit), 500))),
    )

    return [
        {
            "id": row["id"],
            "tier": row["tier"],
            "namespace": row["namespace"],
            "key": row["key"],
            "content": row["content"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
        for row in rows
    ]


@router.post("/{memory_type}")
async def upsert_memory(
    memory_type: str,
    payload: MemoryWrite,
    request: Request,
) -> dict[str, Any]:
    """Create or replace a semantic memory entry.

    `aput` mirrors the canonical /memories/ write into the retrieval table,
    so the same endpoint covers both add and edit (same key overwrites).
    """

    if memory_type != "semantic":
        raise HTTPException(
            status_code=400,
            detail="Only semantic memories can be written through this API",
        )

    provider = request.app.state.memory_provider

    if provider is None:
        raise HTTPException(status_code=503, detail="Memory store is not ready")

    try:
        await provider.semantic.aput(payload.key, payload.content)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return {"ok": True, "key": payload.key, "tier": memory_type}


@router.delete("/{memory_type}/{key}")
async def delete_memory(
    memory_type: str,
    key: str,
    request: Request,
) -> dict[str, Any]:
    """Delete a semantic memory entry by its key."""

    if memory_type != "semantic":
        raise HTTPException(
            status_code=400,
            detail="Only semantic memories can be deleted through this API",
        )

    provider = request.app.state.memory_provider

    if provider is None:
        raise HTTPException(status_code=503, detail="Memory store is not ready")

    try:
        await provider.semantic.adelete(key)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return {"ok": True, "key": key, "tier": memory_type}