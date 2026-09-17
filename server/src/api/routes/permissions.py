"""Permission route handlers.

- GET   /api/v1/permissions          List permission catalog with current modes
- PATCH /api/v1/permissions/{id}     Set a permission's mode (allow/ask/deny)
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Request

from pydantic import BaseModel

router = APIRouter(prefix="/permissions", tags=["permissions"])


class PermissionUpdate(BaseModel):
    mode: Literal["allow", "ask", "deny"]


def _store(request: Request):
    return request.app.state.permission_policy


@router.get("")
async def list_permissions(request: Request) -> dict:
    permissions = await _store(request).list_permissions()

    return {"permissions": permissions}


@router.patch("/{permission_id}")
async def update_permission(
    permission_id: str,
    body: PermissionUpdate,
    request: Request,
) -> dict:
    try:
        await _store(request).set_mode(permission_id, body.mode)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    permissions = await _store(request).list_permissions()

    return {"permissions": permissions}