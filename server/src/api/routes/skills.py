"""Skill route handlers.

- GET  /api/v1/skills              List registered skills
- GET  /api/v1/skills/{id}         Skill detail + versions
- POST /api/v1/skills/{id}/rollback  Rollback to a previous version
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/skills", tags=["skills"])