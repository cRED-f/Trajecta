"""Memory route handlers.

- GET  /api/v1/memory/{type}     Query memory by type (working/episodic/semantic/procedural)
- POST /api/v1/memory/{type}     Add a memory entry
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/memory", tags=["memory"])