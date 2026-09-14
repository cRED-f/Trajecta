"""Model configuration route handlers.

- GET  /api/v1/models     List configured model providers
- POST /api/v1/models     Add or update model provider config
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/models", tags=["models"])