"""Read-only Bifrost gateway status and Trajecta model preferences.

Provider creation, deletion, credentials, routing and fallbacks are managed
exclusively in the Bifrost dashboard. Trajecta only chooses model IDs.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from server.src.llm_gateway.bifrost_admin import (
    BifrostAdminClient, BifrostAdminError,
    classify_provider_type, key_status_ok,
)

router = APIRouter(prefix="/llm", tags=["llm"])


class DefaultModelUpdate(BaseModel):
    default_model: str
    # Legacy clients may continue to send this field; the new UI does not.
    default_provider: str | None = None


def _admin(request: Request) -> BifrostAdminClient:
    return request.app.state.llm_admin


def _store(request: Request):
    return request.app.state.llm_settings


async def _provider_catalog(admin: BifrostAdminClient, healthy: bool) -> list[dict]:
    """Read-only diagnostics; never mutate Bifrost configuration."""
    if not healthy:
        return []
    try:
        providers = await admin.list_providers()
    except BifrostAdminError:
        return []

    result: list[dict] = []
    for entry in providers:
        provider_id = str(entry.get("name") or "").strip()
        if not provider_id:
            continue
        try:
            keys = await admin.provider_keys(provider_id)
        except BifrostAdminError:
            keys = []
        result.append({
            "id": provider_id,
            "type": classify_provider_type(provider_id),
            "configured": True,
            # Key discovery status is diagnostic, not proof inference succeeds.
            "reachable": bool(keys) and all(key_status_ok(key) for key in keys),
        })
    return result


@router.get("")
async def get_llm_catalog(request: Request) -> dict:
    runtime = await _store(request).get()
    admin = _admin(request)
    healthy = await admin.health()
    return {
        "gateway": {
            "type": "bifrost",
            "url": admin.base_url,
            "reachable": healthy,
        },
        "default_provider": runtime["default_provider"],  # legacy read compatibility
        "default_model": runtime["default_model"],
        "providers": await _provider_catalog(admin, healthy),
    }


@router.put("/default")
async def put_default(body: DefaultModelUpdate, request: Request) -> dict:
    """Set the model for NEW conversations; preserve bare routing aliases."""
    model = body.default_model.strip()
    if not model:
        raise HTTPException(status_code=422, detail="default_model is required")
    if len(model) > 200 or any(ch.isspace() for ch in model):
        raise HTTPException(status_code=422, detail="Invalid model id")

    provider = model.split("/", 1)[0] if "/" in model else ""
    if "/" in model and not model.split("/", 1)[1]:
        raise HTTPException(status_code=422, detail="Invalid model id")

    # Preserve the old PUT contract for existing callers, without forcing
    # provider prefixes on new clients that choose a Bifrost-managed alias.
    legacy_provider = (body.default_provider or "").strip()
    if legacy_provider:
        if provider and provider != legacy_provider:
            raise HTTPException(status_code=422, detail="Provider does not match model id")
        if not provider:
            provider = legacy_provider
            model = f"{legacy_provider}/{model}"

    await _store(request).set(default_provider=provider, default_model=model)
    return {"default_provider": provider, "default_model": model}
