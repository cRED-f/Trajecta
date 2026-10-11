"""Control-plane client for the Bifrost management API.

Settings changes flow through Bifrost itself:

    Settings -> Bifrost management API -> Bifrost config DB

config.json is only a first-boot seed: once config.db exists it is
authoritative, so the Settings page never rewrites config.json.
Provider credentials are stored by Bifrost (which redacts them from
management-API responses) and are never persisted in agent_settings.
"""

from __future__ import annotations

import asyncio
import os
from urllib.parse import urlsplit
from typing import Any

import httpx

# Provider types the Settings UI understands:
#   openai / anthropic -> native Bifrost providers (API keys via /keys)
#   ollama             -> native provider pointed at a local base_url
#   openai_compat      -> custom provider routed through an OpenAI-compatible base
PROVIDER_TYPES = frozenset(
    {
        "openai",
        "anthropic",
        "ollama",
        "openai_compat",
    }
)

# Custom provider ids may not collide with Bifrost's built-in providers
# (core/schemas/bifrost.go StandardProviders).
STANDARD_PROVIDER_IDS = frozenset(
    {
        "openai",
        "azure",
        "anthropic",
        "bedrock",
        "bedrock_mantle",
        "cohere",
        "vertex",
        "mistral",
        "ollama",
        "opencode-go",
        "opencode-zen",
        "groq",
        "sgl",
        "parasail",
        "perplexity",
        "cerebras",
        "deepseek",
        "gemini",
        "openrouter",
        "elevenlabs",
        "huggingface",
        "nebius",
        "xai",
        "replicate",
        "vllm",
        "runway",
        "runware",
        "fireworks",
        "sarvam",
        "wafer",
        "github-copilot",
        "databricks",
        "typesafe",
    }
)

# Bifrost and Ollama run as native local processes. No container host alias.
DEFAULT_OLLAMA_BASE_URL = "http://127.0.0.1:11434"

# The seeded Trajecta virtual key. It must allow every provider so a
# provider added from Settings immediately works for chat.
TRAJECTA_VIRTUAL_KEY_ID = "vk-trajecta-local"
TRAJECTA_VIRTUAL_KEY_NAME = "trajecta-local"

# PUT /api/providers/{p} rejects zero/absent concurrency settings.
_FALLBACK_CONCURRENCY = {
    "concurrency": 1000,
    "buffer_size": 5000,
}


class BifrostAdminError(RuntimeError):
    """A Bifrost management API call failed."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code


def classify_provider_type(provider_id: str) -> str:
    """Map a Bifrost provider id to the Settings type tag."""
    if provider_id == "ollama":
        return "ollama"
    if provider_id in ("openai", "anthropic"):
        return provider_id
    return "openai_compat"


def key_status_ok(key: dict) -> bool:
    """Whether a provider key's discovery status counts as healthy.

    Fresh keys report "" until discovery runs, so only explicit
    failures (e.g. "list_models_failed") mark the key unreachable.
    """
    status = str(key.get("status") or "").strip().lower()
    return "fail" not in status and "error" not in status


def _ollama_key_url(key: dict) -> str:
    """The endpoint stored on an Ollama key, or "" when unset.

    Bifrost reports secrets as ``{"value": ..., "type": ...}`` and
    redacts them; the URL is not a credential, so it comes back whole.
    """
    config = key.get("ollama_key_config")
    if not isinstance(config, dict):
        return ""
    url: Any = config.get("url")
    if isinstance(url, dict):
        url = url.get("value")
    return str(url or "")


def _concurrency_from(stored: Any) -> dict:
    if isinstance(stored, dict):
        try:
            concurrency = int(stored.get("concurrency") or 0)
            buffer_size = int(stored.get("buffer_size") or 0)
        except (TypeError, ValueError):
            concurrency = buffer_size = 0
        if concurrency > 0 and buffer_size > 0 and concurrency <= buffer_size:
            return {
                "concurrency": concurrency,
                "buffer_size": buffer_size,
            }
    return dict(_FALLBACK_CONCURRENCY)


class BifrostAdminClient:
    """Thin async client over Bifrost's management (dashboard) API."""

    def __init__(
        self,
        base_url: str | None,
        *,
        timeout: float = 15.0,
    ) -> None:
        self._base_url = (base_url or "").rstrip("/")
        self._timeout = timeout

    @property
    def base_url(self) -> str | None:
        return self._base_url or None

    # ------------------------------------------------------------------
    # HTTP plumbing
    # ------------------------------------------------------------------

    def _require_base(self) -> str:
        if not self._base_url:
            raise BifrostAdminError(
                "Bifrost URL is not configured. "
                "Set TRAJECTA_BIFROST_URL or BIFROST_URL."
            )
        return self._base_url

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict | None = None,
    ) -> tuple[int, Any]:
        base = self._require_base()
        # Only attach the local installation's setup token to its loopback gateway.
        # Never forward this administrative secret to remote Bifrost instances.
        headers: dict[str, str] = {}
        parsed = urlsplit(base)
        if (parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"}
                and parsed.port == 8080):
            token = os.environ.get("TRAJECTA_BIFROST_SETUP_TOKEN", "")
            if token and len(token) == 64 and all(c in "0123456789abcdefABCDEF" for c in token):
                headers["X-Bifrost-Setup-Token"] = token
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout,
            ) as client:
                response = await client.request(
                    method,
                    f"{base}{path}",
                    json=json,
                    headers=headers,
                )
        except httpx.HTTPError as exc:
            raise BifrostAdminError(
                f"Bifrost gateway is unreachable ({exc})"
            ) from exc

        if response.status_code == 204:
            return 204, None

        try:
            return response.status_code, response.json()
        except ValueError:
            return response.status_code, response.text

    @staticmethod
    def _raise(message: str, status: int, payload: Any) -> None:
        detail: Any = None
        if isinstance(payload, dict):
            detail = (
                payload.get("error")
                or payload.get("message")
                or payload.get("detail")
            )
        elif isinstance(payload, str) and payload.strip():
            detail = payload.strip()[:300]
        raise BifrostAdminError(
            f"{message}: {detail}" if detail else message,
            status_code=status,
        )

    def _expect(
        self,
        message: str,
        status: int,
        payload: Any,
        *,
        ok: tuple[int, ...] = (200, 201),
    ) -> Any:
        if status not in ok:
            self._raise(message, status, payload)
        return payload

    @staticmethod
    def _unwrap_provider(payload: Any) -> dict | None:
        if not isinstance(payload, dict):
            return None
        inner = payload.get("provider")
        if isinstance(inner, dict):
            return inner
        return payload

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def health(self) -> bool:
        """Cheap liveness probe for the gateway card."""
        try:
            status, payload = await self._request("GET", "/health")
        except BifrostAdminError:
            return False
        if status != 200:
            return False
        if isinstance(payload, dict):
            return str(payload.get("status") or "").lower() == "ok"
        return False

    async def list_providers(self) -> list[dict]:
        status, payload = await self._request("GET", "/api/providers")
        self._expect("Listing Bifrost providers failed", status, payload)
        items = payload.get("providers") if isinstance(payload, dict) else None
        return [
            item
            for item in (items or [])
            if isinstance(item, dict) and item.get("name")
        ]

    async def get_provider(self, provider: str) -> dict | None:
        status, payload = await self._request(
            "GET",
            f"/api/providers/{provider}",
        )
        if status == 404:
            return None
        self._expect(
            f"Reading provider {provider!r} failed",
            status,
            payload,
        )
        return self._unwrap_provider(payload)

    async def provider_keys(self, provider: str) -> list[dict]:
        status, payload = await self._request(
            "GET",
            f"/api/providers/{provider}/keys",
        )
        if status == 404:
            return []
        self._expect(
            f"Reading keys for provider {provider!r} failed",
            status,
            payload,
        )
        keys = payload.get("keys") if isinstance(payload, dict) else None
        return [key for key in (keys or []) if isinstance(key, dict)]

    # ------------------------------------------------------------------
    # Provider writes
    # ------------------------------------------------------------------

    def _create_payload(
        self,
        *,
        provider: str,
        provider_type: str,
        base_url: str | None,
        extra_headers: dict[str, str] | None,
    ) -> dict:
        body: dict[str, Any] = {"provider": provider}

        if base_url:
            body["network_config"] = {
                "base_url": base_url,
                # Settings accepts LAN/local endpoints (localhost Ollama,
                # local service communication), so loopback/private IPs must pass.
                "allow_private_network": True,
                **(
                    {"extra_headers": extra_headers}
                    if extra_headers
                    else {}
                ),
            }

        if provider_type == "openai_compat":
            body["custom_provider_config"] = {
                "base_provider_type": "openai",
            }

        return body

    def _update_payload(
        self,
        *,
        existing: dict,
        provider_type: str,
        base_url: str | None,
        extra_headers: dict[str, str] | None,
    ) -> dict:
        # PUT overwrites network_config wholesale (omitted fields decode to
        # zero values), so start from the stored config and overlay changes.
        network = dict(existing.get("network_config") or {})

        if base_url:
            network["base_url"] = base_url
            network["allow_private_network"] = True

        if extra_headers is not None:
            network["extra_headers"] = extra_headers

        body: dict[str, Any] = {
            "network_config": network,
            # Required on PUT: both fields must be > 0 and
            # concurrency <= buffer_size.
            "concurrency_and_buffer_size": _concurrency_from(
                existing.get("concurrency_and_buffer_size")
            ),
        }

        # Omitted nested blocks mean "leave this alone" — only carry
        # custom_provider_config when the provider type needs it.
        if provider_type == "openai_compat":
            body["custom_provider_config"] = {
                "base_provider_type": "openai",
            }

        return body

    async def upsert_provider(
        self,
        *,
        provider: str,
        provider_type: str,
        base_url: str | None = None,
        extra_headers: dict[str, str] | None = None,
        api_key: str | None = None,
    ) -> None:
        """Create or update a provider and its API key in Bifrost."""
        if provider_type == "ollama":
            base_url = base_url or DEFAULT_OLLAMA_BASE_URL

        existing = await self.get_provider(provider)

        if existing is None:
            status, payload = await self._request(
                "POST",
                "/api/providers",
                json=self._create_payload(
                    provider=provider,
                    provider_type=provider_type,
                    base_url=base_url,
                    extra_headers=extra_headers,
                ),
            )
            # 409 = created concurrently; fall through to the update path.
            if status == 409:
                existing = await self.get_provider(provider)
            else:
                self._expect(
                    f"Creating provider {provider!r} failed",
                    status,
                    payload,
                )
                existing = await self.get_provider(provider)

        if existing is not None:
            status, payload = await self._request(
                "PUT",
                f"/api/providers/{provider}",
                json=self._update_payload(
                    existing=existing,
                    provider_type=provider_type,
                    base_url=base_url,
                    extra_headers=extra_headers,
                ),
            )
            self._expect(
                f"Updating provider {provider!r} failed",
                status,
                payload,
            )

        await self._ensure_key(
            provider,
            provider_type=provider_type,
            api_key=api_key,
            base_url=base_url,
        )

    async def _ensure_key(
        self,
        provider: str,
        *,
        provider_type: str,
        api_key: str | None,
        base_url: str | None = None,
    ) -> None:
        """Create or rotate the provider's API key.

        With no api_key from the caller, existing keys are left alone.
        Ollama needs no credential but does need a key row, so it takes
        its own path (_ensure_ollama_key) to carry the endpoint URL.
        """
        keys = await self.provider_keys(provider)

        if provider_type == "ollama":
            await self._ensure_ollama_key(
                provider,
                keys=keys,
                api_key=api_key,
                base_url=base_url,
            )
            return

        if not api_key:
            return

        if not keys:
            status, payload = await self._request(
                "POST",
                f"/api/providers/{provider}/keys",
                json={
                    "name": f"{provider}-local",
                    "value": api_key,
                    "models": ["*"],
                    "weight": 1.0,
                },
            )
            self._expect(
                f"Creating a key for provider {provider!r} failed",
                status,
                payload,
            )
            return

        # Rotate the first key; Bifrost merges partial key updates, so
        # only the value changes and weight/models are preserved.
        key_id = str(keys[0].get("id") or "")
        if not key_id:
            raise BifrostAdminError(
                f"Provider {provider!r} has a key without an id"
            )
        status, payload = await self._request(
            "PUT",
            f"/api/providers/{provider}/keys/{key_id}",
            json={"value": api_key},
        )
        self._expect(
            f"Updating the key for provider {provider!r} failed",
            status,
            payload,
        )

    async def _ensure_ollama_key(
        self,
        provider: str,
        *,
        keys: list[dict],
        api_key: str | None,
        base_url: str | None,
    ) -> None:
        """Give Ollama a key row carrying its endpoint URL.

        Ollama needs no credential, but Bifrost still requires a key
        record — and rejects it without ``ollama_key_config.url``. The
        provider's network_config does not cover native Ollama model
        discovery or routing, so the URL has to live on the key itself.

        Unlike other providers this write is a replace, not a merge:
        name, models and weight must ride along or they reset.
        """
        url = base_url or DEFAULT_OLLAMA_BASE_URL
        stored = keys[0] if keys else None

        if (
            stored is not None
            and not api_key
            and _ollama_key_url(stored) == url
        ):
            # Already pointed at the right endpoint; leave it alone.
            return

        body: dict[str, Any] = {
            "name": (stored or {}).get("name") or f"{provider}-local",
            "value": api_key or "",
            "models": (stored or {}).get("models") or ["*"],
            "weight": (stored or {}).get("weight") or 1.0,
            "ollama_key_config": {"url": url},
        }

        if stored is None:
            status, payload = await self._request(
                "POST",
                f"/api/providers/{provider}/keys",
                json=body,
            )
            self._expect(
                f"Creating a key for provider {provider!r} failed",
                status,
                payload,
            )
            return

        key_id = str(stored.get("id") or "")
        if not key_id:
            raise BifrostAdminError(
                f"Provider {provider!r} has a key without an id"
            )
        status, payload = await self._request(
            "PUT",
            f"/api/providers/{provider}/keys/{key_id}",
            json=body,
        )
        self._expect(
            f"Updating the key for provider {provider!r} failed",
            status,
            payload,
        )

    async def remove_provider(self, provider: str) -> bool:
        """Delete a provider; returns False when it does not exist."""
        status, payload = await self._request(
            "DELETE",
            f"/api/providers/{provider}",
        )
        if status == 404:
            return False
        self._expect(
            f"Deleting provider {provider!r} failed",
            status,
            payload,
            ok=(200, 204),
        )
        return True

    async def ensure_allow_all_providers(self) -> None:
        """Keep the Trajecta virtual key open to every configured provider."""
        status, payload = await self._request(
            "GET",
            "/api/governance/virtual-keys?limit=100",
        )
        self._expect("Listing virtual keys failed", status, payload)

        keys = (
            payload.get("virtual_keys")
            if isinstance(payload, dict)
            else None
        ) or []
        keys = [key for key in keys if isinstance(key, dict)]

        target = next(
            (
                key
                for key in keys
                if str(key.get("id")) == TRAJECTA_VIRTUAL_KEY_ID
                or str(key.get("name")) == TRAJECTA_VIRTUAL_KEY_NAME
            ),
            keys[0] if len(keys) == 1 else None,
        )
        if target is None:
            # Ambiguous governance setup — leave operator-managed keys alone.
            return

        if bool(target.get("allow_all_providers")):
            return

        status, payload = await self._request(
            "PUT",
            f"/api/governance/virtual-keys/{target.get('id')}",
            json={"allow_all_providers": True},
        )
        self._expect(
            "Updating the Trajecta virtual key failed",
            status,
            payload,
        )

    # ------------------------------------------------------------------
    # Models and connection tests
    # ------------------------------------------------------------------

    async def provider_models(self, provider: str) -> list[str]:
        """Known models as provider-prefixed ids (the /v1 format)."""
        status, payload = await self._request(
            "GET",
            f"/api/models?provider={provider}&limit=1000",
        )
        self._expect(
            f"Listing models for provider {provider!r} failed",
            status,
            payload,
        )
        items = payload.get("models") if isinstance(payload, dict) else None

        models: list[str] = []
        for item in items or []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            owner = str(item.get("provider") or provider).strip() or provider
            # /api/models returns bare names ("af/gpt-oss-120b"); chat
            # and /v1 expect "provider/name".
            if name.startswith(f"{owner}/"):
                models.append(name)
            else:
                models.append(f"{owner}/{name}")
        return models

    async def test_provider(self, provider: str) -> dict:
        """Live connection test: refresh model discovery, read key status."""
        keys = await self.provider_keys(provider)
        if not keys:
            return {
                "reachable": False,
                "status": "no_key",
                "detail": "No API key configured for this provider.",
            }

        status: int | None = None
        payload: Any = None
        for attempt in (0, 1):
            status, payload = await self._request(
                "POST",
                f"/api/providers/{provider}/refresh-models",
            )
            # 409 = a refresh is already running; retry once.
            if status == 409 and attempt == 0:
                await asyncio.sleep(0.5)
                continue
            break

        assert status is not None
        if status == 404:
            raise BifrostAdminError(
                f"Provider {provider!r} is not configured",
                status_code=404,
            )
        if status >= 400:
            return {
                "reachable": False,
                "status": "error",
                "detail": self._detail(payload)
                or f"Bifrost returned HTTP {status}.",
            }

        refreshed = (
            payload.get("keys")
            if isinstance(payload, dict)
            else None
        )
        checked = [
            key
            for key in (refreshed or keys)
            if isinstance(key, dict)
        ]
        failing = [
            key
            for key in checked
            if not key_status_ok(key)
        ]
        if not checked or failing:
            detail = "; ".join(
                filter(
                    None,
                    (
                        str(key.get("description") or key.get("status") or "")
                        for key in (failing or checked)
                    ),
                )
            )
            return {
                "reachable": False,
                "status": "failed",
                "detail": detail or "Model discovery failed.",
            }

        return {
            "reachable": True,
            "status": "success",
            "detail": f"Discovered {len(checked)} key(s); models refreshed.",
        }

    @staticmethod
    def _detail(payload: Any) -> str | None:
        if isinstance(payload, dict):
            detail = (
                payload.get("error")
                or payload.get("message")
                or payload.get("detail")
            )
            return str(detail) if detail else None
        if isinstance(payload, str) and payload.strip():
            return payload.strip()[:300]
        return None
