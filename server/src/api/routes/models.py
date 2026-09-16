"""Model selection and discovery APIs."""

from __future__ import annotations

from fastapi import APIRouter, Request

from server.src.chat.models import ModelCatalog

router = APIRouter(prefix="/models", tags=["models"])


@router.get("", response_model=ModelCatalog)
async def list_models(request: Request) -> ModelCatalog:
    return await request.app.state.model_catalog.list_models()
