from __future__ import annotations

import os
from typing import Any

import httpx

from server.src.chat.model import BifrostModelFactory
from server.src.chat.models import ModelCatalog, ModelInfo
from server.src.config import Settings


class ModelCatalogService:
    """Best-effort model discovery through Bifrost with configured fallbacks."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._factory = BifrostModelFactory(settings)

    async def list_models(self) -> ModelCatalog:
        default_model = (
            self._factory
            .canonical_model_name(
                self._settings.chat.default_model
            )
        )

        configured: dict[str, ModelInfo] = {
            default_model: ModelInfo(
                id=default_model,
                provider=default_model.split("/", 1)[0],
                source="configured",
            )
        }

        gateway_reachable = False
        gateway_models: dict[str, ModelInfo] = {}
        base = self._factory.gateway_base_url()
        if base:
            headers: dict[str, str] = {}
            key = self._factory.virtual_key()
            if key:
                headers["x-bf-vk"] = key
                headers["Authorization"] = f"Bearer {key}"
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    response = await client.get(f"{base.rstrip('/')}/v1/models", headers=headers)
                    if response.status_code < 500:
                        gateway_reachable = True
                    response.raise_for_status()
                    payload: Any = response.json()
                    items = payload.get("data", []) if isinstance(payload, dict) else []
                    for item in items:
                        if not isinstance(item, dict) or not item.get("id"):
                            continue
                        raw_model_id = str(item["id"]).strip()
                        model_id = self._factory.canonical_model_name(raw_model_id)
                        gateway_models[model_id] = ModelInfo(
                            id=model_id,
                            provider=model_id.split("/", 1)[0],
                            source="bifrost",
                            owned_by=(str(item.get("owned_by")) if item.get("owned_by") else None),
                            metadata={
                                key: value
                                for key, value in item.items()
                                if key not in {"id", "owned_by"}
                            },
                        )
            except (httpx.HTTPError, ValueError):
                pass

        merged = {**configured, **gateway_models}
        return ModelCatalog(
            default_model=default_model,
            models=sorted(merged.values(), key=lambda model: model.id),
            gateway_reachable=gateway_reachable,
        )
