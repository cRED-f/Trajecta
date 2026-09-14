"""Tool route handlers.

- GET  /api/v1/tools     List configured tools (built-in + MCP)
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/tools", tags=["tools"])