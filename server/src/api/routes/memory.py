"""Memory route handlers.

- GET   /api/v1/memory/{type}     List memory entries by type (semantic/episodic/procedural)
- POST  /api/v1/memory/{type}     Upsert a semantic memory entry (key + content)
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

router = APIRouter(prefix="/memory", tags=["memory"])

_KNOWN_TIERS = {"semantic", "episodic", "procedural"}


class MemoryWrite(BaseModel):
    key: str = Field(..., min_length=1, max_length=250)
    content: str = Field(..., min_length=1, max_length=100_000)


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

    if provider is None or provider.fts is None:
        raise HTTPException(status_code=503, detail="Memory store is not ready")

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