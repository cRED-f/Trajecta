"""Task route handlers.

- POST /api/v1/tasks          Submit a new task
- GET  /api/v1/tasks          List tasks
- GET  /api/v1/tasks/{id}     Task status + result
- GET  /api/v1/tasks/{id}/stream   SSE stream of agent events
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/tasks", tags=["tasks"])