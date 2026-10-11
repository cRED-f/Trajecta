from __future__ import annotations

import os
from typing import Any

import httpx

from server.src.chat.model import BifrostModelFactory
from server.src.chat.models import ModelCatalog, ModelInfo
from server.src.config import Settings
from server.src.llm_gateway.bifrost_admin import (
    BifrostAdminClient,
    BifrostAdminError,
)
from server.src.llm_gateway.settings import LLMSettingsStore


class ModelCatalogService:
    """Best-effort model discovery through Bifrost with configured fallbacks."""

    def __init__(
        self,
        settings: Settings,
        *,
        admin: BifrostAdminClient | None = None,
        runtime_settings: LLMSettingsStore | None = None,
    ) -> None:
        self._settings = settings
        self._factory = BifrostModelFactory(settings)
        self._admin = admin
        self._runtime_settings = runtime_settings

    async def list_models(self) -> ModelCatalog:
        configured_default = self._settings.chat.default_model
        if self._runtime_settings is not None:
            configured_default = (await self._runtime_settings.get())["default_model"]
        default_model = self._factory.canonical_model_name(configured_default)

        configured: dict[str, ModelInfo] = {
            default_model: ModelInfo(
                id=default_model,
                provider=default_model.split("/", 1)[0] if "/" in default_model else None,
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
                            provider=model_id.split("/", 1)[0] if "/" in model_id else None,
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

        # Also read Bifrost's management model catalog.
        #
        # Settings already uses this catalog, so merging it
        # here keeps the Chat model selector consistent with
        # Settings after dynamically adding providers such
        # as Ollama.
        if self._admin is not None:
            try:
                if await self._admin.health():
                    gateway_reachable = True
                    providers = await self._admin.list_providers()
                    for entry in providers:
                        provider = str(entry.get("name") or "").strip()
                        if not provider:
                            continue
                        try:
                            discovered = await self._admin.provider_models(
                                provider
                            )
                        except BifrostAdminError:
                            continue
                        for raw_model_id in discovered:
                            model_id = str(raw_model_id).strip()
                            if model_id and "/" not in model_id:
                                model_id = f"{provider}/{model_id}"
                            if not model_id:
                                continue
                            # /v1/models carries owned_by and the rest of
                            # the payload, so keep it when both catalogs
                            # know the same model.
                            gateway_models.setdefault(
                                model_id,
                                ModelInfo(
                                    id=model_id,
                                    provider=model_id.split("/", 1)[0] if "/" in model_id else None,
                                    source="bifrost",
                                ),
                            )
            except BifrostAdminError:
                pass

        merged = {**configured, **gateway_models}
        return ModelCatalog(
            default_model=default_model,
            models=sorted(merged.values(), key=lambda model: model.id),
            gateway_reachable=gateway_reachable,
        )
