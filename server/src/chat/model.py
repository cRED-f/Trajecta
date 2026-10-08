from __future__ import annotations

import os

from langchain_openai import ChatOpenAI

from server.src.config import Settings


class BifrostConfigurationError(
    RuntimeError
):
    pass


class BifrostModelFactory:
    """
    Build LangChain models through Bifrost.

    Deep Agents needs a real LangChain chat model because tool
    binding and streaming happen at the LangChain model layer.

    Bifrost remains responsible for:
        provider routing
        fallback
        provider credentials
        usage
        governance
    """

    def __init__(
        self,
        settings: Settings,
    ) -> None:
        self._settings = settings

    def create(
        self,
        model_name: str | None = None,
    ) -> ChatOpenAI:
        bifrost_url = self.gateway_base_url()

        if not bifrost_url:
            raise BifrostConfigurationError(
                "Trajecta chat requires Bifrost. "
                "Set TRAJECTA_BIFROST_URL or enable "
                "llm.providers.bifrost.base_url."
            )

        virtual_key = self.virtual_key()

        model = self.canonical_model_name(
            model_name
        )

        stripped = bifrost_url.rstrip("/")

        base_url = (
            stripped
            if stripped.endswith("/v1")
            else f"{stripped}/v1"
        )

        return ChatOpenAI(
            model=model,
            base_url=base_url,
            api_key=virtual_key,
            default_headers={"x-bf-vk": virtual_key},
            use_responses_api=False,
            max_retries=0,
            timeout=180,
        )

    def gateway_base_url(self) -> str | None:
        return (
            os.environ.get("TRAJECTA_BIFROST_URL")
            or os.environ.get("BIFROST_URL")
            or self._configured_url()
        )

    @staticmethod
    def virtual_key() -> str:
        return os.environ.get("BIFROST_VIRTUAL_KEY", "sk-bf-trajecta")

    def _configured_url(
        self,
    ) -> str | None:
        config = (
            self._settings
            .llm
            .providers
            .get("bifrost")
        )

        if (
            config is None
            or not config.enabled
        ):
            return None

        return config.base_url

    def canonical_model_name(
        self,
        model_name: str | None,
    ) -> str:
        """
        Return an explicit Bifrost provider/model identifier.

        Bare model names are repaired using the provider from
        chat.default_model. This is especially important for custom
        OpenAI-compatible providers such as 9router.
        """
        model = (
            model_name
            or self._settings.chat.default_model
        ).strip()

        if not model:
            raise BifrostConfigurationError(
                "Model cannot be empty"
            )

        # Already explicit.
        if "/" in model:
            return model

        default_model = (
            self._settings.chat.default_model.strip()
        )

        if "/" not in default_model:
            raise BifrostConfigurationError(
                "Bifrost model must use provider/model format; "
                f"got {model!r}. Configure chat.default_model "
                "with an explicit provider."
            )

        provider = (
            default_model.split("/", 1)[0]
        )

        return f"{provider}/{model}"

    async def resolve_or_default(
        self,
        model_name: str | None = None,
        *,
        validate_catalog: bool = True,
    ) -> str:
        """Resolve and validate a model without silently changing providers."""
        canonical = self.canonical_model_name(
            model_name
        )
        # Chat's selected provider/model is already explicit. Doing a network
        # /v1/models request for each turn adds seconds before SSE even begins.
        # The settings/model-selection API still validates the catalog when
        # requested; inference is the final source of model availability.
        if not validate_catalog:
            return canonical
        provider = canonical.split(
            "/",
            1,
        )[0]
        known = await self._known_models(
            provider
        )
        # If Bifrost model discovery itself is unavailable,
        # let the actual inference request determine whether
        # the model can be used.
        if known is None:
            return canonical
        if canonical not in known:
            raise BifrostConfigurationError(
                (
                    f"Selected model {canonical!r} "
                    f"is not available from provider "
                    f"{provider!r}. Refresh the provider's "
                    "model list and try again."
                )
            )
        return canonical

    async def _known_models(
        self,
        provider: str,
    ) -> set[str] | None:
        import httpx

        base = self.gateway_base_url()
        if not base:
            return None
        headers: dict[str, str] = {}
        key = self.virtual_key()
        if key:
            headers["x-bf-vk"] = key
            headers["Authorization"] = f"Bearer {key}"
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(
                    f"{base.rstrip('/')}/v1/models",
                    params={"provider": provider},
                    headers=headers,
                )
                response.raise_for_status()
                payload = response.json()
                items = (
                    payload.get("data", [])
                    if isinstance(payload, dict)
                    else []
                )
                known: set[str] = set()
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    model_id = str(item.get("id") or "").strip()
                    if not model_id:
                        continue
                    # The catalog is scoped to `provider`, so a bare id
                    # belongs to that provider — never to whatever
                    # chat.default_model happens to point at.
                    if "/" not in model_id:
                        model_id = f"{provider}/{model_id}"
                    known.add(model_id)
                return known
        except Exception:
            return None
