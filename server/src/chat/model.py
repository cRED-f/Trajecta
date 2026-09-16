from __future__ import annotations

import logging
import os

from langchain_openai import ChatOpenAI

from server.src.config import Settings

logger = logging.getLogger(__name__)


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
    ) -> str:
        """
        Validate model against Bifrost catalog; fall back to default
        when the model does not exist (stale/unknown model names).
        """
        canonical = self.canonical_model_name(model_name)
        known = await self._known_models()
        if known is not None and canonical not in known:
            default = self.canonical_model_name(None)
            logger.warning(
                "Model %r not found in Bifrost catalog; "
                "falling back to default %r",
                canonical,
                default,
            )
            return default
        return canonical

    async def _known_models(self) -> set[str] | None:
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
                    headers=headers,
                )
                response.raise_for_status()
                payload = response.json()
                items = (
                    payload.get("data", [])
                    if isinstance(payload, dict)
                    else []
                )
                return {
                    self.canonical_model_name(str(item["id"]))
                    for item in items
                    if isinstance(item, dict) and item.get("id")
                }
        except Exception:
            return None
