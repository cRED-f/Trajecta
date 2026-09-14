"""Health check route.

- GET  /api/v1/health     Health check
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/health", tags=["health"])


@router.get("")
def health() -> dict[str, str]:
    """Readiness probe."""
    return {"status": "ok", "service": "trajecta"}