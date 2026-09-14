"""Trace route handlers.

- GET  /api/v1/traces     Recent agent traces
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/traces", tags=["traces"])