"""Health check route.

- GET  /api/v1/health     Health check
"""

from __future__ import annotations

import os

from fastapi import APIRouter

router = APIRouter(prefix="/health", tags=["health"])


@router.get("")
def health() -> dict[str, str]:
    """Readiness probe."""
    result = {"status": "ok", "service": "trajecta"}
    # Nonsecret installation marker, not an authentication credential. Only
    # managed desktop processes set it; dev servers retain the old response.
    instance_id = os.environ.get("TRAJECTA_BACKEND_INSTANCE_ID")
    if instance_id:
        result["instance_id"] = instance_id
    return result