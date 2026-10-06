"""LLM gateway (Bifrost) settings APIs.

The gateway itself is fixed — Bifrost is the only gateway. What the
user chooses here is the provider behind Bifrost, the model behind
that provider, and the global default applied to NEW conversations.
Per-conversation model selection is unchanged (PUT /chat/.../model).
"""

from __future__ import annotations

from typing import Literal

from fastapi import (
    APIRouter,
    HTTPException,
    Request,
)
from pydantic import (
    BaseModel,
)

from server.src.llm_gateway.bifrost_admin import (
    DEFAULT_OLLAMA_BASE_URL,
    STANDARD_PROVIDER_IDS,
    BifrostAdminClient,
    BifrostAdminError,
    classify_provider_type,
    key_status_ok,
)

router = APIRouter(
    prefix="/llm",
    tags=["llm"],
)

# Always listed in Settings even before they are configured.
TEMPLATE_PROVIDER_IDS = (
    "9router",
    "openai",
    "anthropic",
    "ollama",
)


class ProviderUpsert(BaseModel):
    type: Literal[
        "openai",
        "anthropic",
        "ollama",
        "openai_compat",
    ]
    base_url: str | None = None
    # Forwarded to Bifrost only; never persisted in agent_settings.
    api_key: str | None = None
    extra_headers: dict[str, str] | None = None


class DefaultModelUpdate(BaseModel):
    default_provider: str
    default_model: str


def _admin(request: Request) -> BifrostAdminClient:
    return request.app.state.llm_admin


def _store(request: Request):
    return request.app.state.llm_settings


def _map_error(exc: BifrostAdminError) -> HTTPException:
    return HTTPException(
        status_code=exc.status_code or 502,
        detail=str(exc),
    )


async def _configured_providers(
    admin: BifrostAdminClient,
    gateway_ok: bool,
) -> dict[str, dict]:
    if not gateway_ok:
        return {}
    try:
        items = await admin.list_providers()
    except BifrostAdminError:
        return {}
    return {
        str(item.get("name")): item
        for item in items
        if item.get("name")
    }


async def _provider_entry(
    admin: BifrostAdminClient,
    provider_id: str,
    *,
    gateway_ok: bool,
    configured: dict[str, dict],
) -> dict:
    is_configured = provider_id in configured
    reachable = False

    if is_configured and gateway_ok:
        try:
            keys = await admin.provider_keys(provider_id)
        except BifrostAdminError:
            keys = []
        reachable = bool(keys) and all(
            key_status_ok(key) for key in keys
        )

    return {
        "id": provider_id,
        "type": classify_provider_type(provider_id),
        "configured": is_configured,
        "reachable": reachable,
    }


async def _catalog(admin: BifrostAdminClient) -> tuple[bool, list[dict]]:
    gateway_ok = await admin.health()
    configured = await _configured_providers(admin, gateway_ok)

    provider_ids = list(
        dict.fromkeys(
            [
                *TEMPLATE_PROVIDER_IDS,
                *configured.keys(),
            ]
        )
    )

    entries = [
        await _provider_entry(
            admin,
            provider_id,
            gateway_ok=gateway_ok,
            configured=configured,
        )
        for provider_id in provider_ids
    ]
    return gateway_ok, entries


def _single_entry(
    provider_id: str,
    provider_type: str,
    *,
    existing: dict | None,
    keys: list[dict],
) -> dict:
    is_configured = existing is not None
    return {
        "id": provider_id,
        "type": provider_type,
        "configured": is_configured,
        "reachable": is_configured
        and bool(keys)
        and all(key_status_ok(key) for key in keys),
    }


async def _require_provider(
    admin: BifrostAdminClient,
    provider: str,
) -> dict:
    existing = await admin.get_provider(provider)
    if existing is None:
        raise HTTPException(
            status_code=404,
            detail=f"Provider {provider!r} is not configured",
        )
    return existing


@router.get("")
async def get_llm_catalog(request: Request) -> dict:
    """Gateway status, defaults, and the provider catalog."""
    runtime = await _store(request).get()
    gateway_ok, providers = await _catalog(_admin(request))

    return {
        "gateway": {
            "type": "bifrost",
            "url": _admin(request).base_url,
            "reachable": gateway_ok,
        },
        "default_provider": runtime["default_provider"],
        "default_model": runtime["default_model"],
        "providers": providers,
    }


@router.get("/providers")
async def list_providers(request: Request) -> dict:
    _, providers = await _catalog(_admin(request))
    return {"providers": providers}


@router.put("/providers/{provider}")
async def upsert_provider(
    provider: str,
    body: ProviderUpsert,
    request: Request,
) -> dict:
    """Create or reconfigure a provider behind Bifrost."""
    provider = provider.strip()
    if not provider:
        raise HTTPException(
            status_code=422,
            detail="Provider id cannot be empty",
        )

    provider_type = body.type

    # Native providers live at their fixed id; anything else is a
    # custom OpenAI-compatible provider with a free-form id.
    if provider_type in ("openai", "anthropic", "ollama"):
        if provider != provider_type:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Type {provider_type!r} must be configured at "
                    f"/providers/{provider_type}"
                ),
            )
    elif provider in STANDARD_PROVIDER_IDS:
        raise HTTPException(
            status_code=422,
            detail=(
                f"{provider!r} is a built-in Bifrost provider; "
                "choose a different provider id"
            ),
        )

    base_url = (body.base_url or "").strip() or None
    if provider_type == "ollama" and not base_url:
        base_url = DEFAULT_OLLAMA_BASE_URL
    if provider_type == "openai_compat" and not base_url:
        raise HTTPException(
            status_code=422,
            detail="base_url is required for OpenAI-compatible providers",
        )

    admin = _admin(request)
    try:
        await admin.upsert_provider(
            provider=provider,
            provider_type=provider_type,
            base_url=base_url,
            extra_headers=body.extra_headers,
            api_key=body.api_key,
        )
        # Providers added here must be usable by the chat virtual key.
        await admin.ensure_allow_all_providers()
        existing = await admin.get_provider(provider)
        keys = (
            await admin.provider_keys(provider)
            if existing is not None
            else []
        )
    except BifrostAdminError as exc:
        raise _map_error(exc) from exc

    return _single_entry(
        provider,
        provider_type,
        existing=existing,
        keys=keys,
    )


@router.delete("/providers/{provider}")
async def remove_provider(
    provider: str,
    request: Request,
) -> dict:
    admin = _admin(request)
    try:
        removed = await admin.remove_provider(provider)
    except BifrostAdminError as exc:
        raise _map_error(exc) from exc

    if not removed:
        raise HTTPException(
            status_code=404,
            detail=f"Provider {provider!r} is not configured",
        )
    return {"deleted": provider}


@router.get("/providers/{provider}/models")
async def provider_models(
    provider: str,
    request: Request,
) -> dict:
    admin = _admin(request)
    try:
        await _require_provider(admin, provider)
        models = await admin.provider_models(provider)
    except BifrostAdminError as exc:
        raise _map_error(exc) from exc
    return {"models": models}


@router.post("/providers/{provider}/test")
async def test_provider(
    provider: str,
    request: Request,
) -> dict:
    admin = _admin(request)
    try:
        await _require_provider(admin, provider)
        result = await admin.test_provider(provider)
    except BifrostAdminError as exc:
        raise _map_error(exc) from exc
    return result


@router.put("/default")
async def put_default(
    body: DefaultModelUpdate,
    request: Request,
) -> dict:
    """Persist the global default for NEW conversations."""
    provider = body.default_provider.strip()
    model = body.default_model.strip()

    if not provider or not model:
        raise HTTPException(
            status_code=422,
            detail="default_provider and default_model are required",
        )

    if "/" not in model:
        model = f"{provider}/{model}"
    elif model.split("/", 1)[0] != provider:
        raise HTTPException(
            status_code=422,
            detail=(
                f"default_model {model!r} must start with "
                f"{provider}/"
            ),
        )

    await _store(request).set(
        default_provider=provider,
        default_model=model,
    )

    return {
        "default_provider": provider,
        "default_model": model,
    }
