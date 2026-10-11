from __future__ import annotations

import os

from langchain_openai import ChatOpenAI
from langchain_core.messages import AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk

from server.src.config import Settings


class BifrostConfigurationError(
    RuntimeError
):
    pass


class BifrostReasoningChatOpenAI(ChatOpenAI):
    """Preserve optional reasoning deltas returned by OpenAI-compatible gateways.

    ChatOpenAI intentionally supports the standard OpenAI response fields and
    can discard provider-specific `reasoning_content` / `reasoning` fields.
    This narrow override keeps those fields without changing model routing,
    tool calls, token accounting, or the normal assistant answer.
    """

    def _convert_chunk_to_generation_chunk(
        self,
        chunk: dict,
        default_chunk_class: type,
        base_generation_info: dict | None,
    ) -> ChatGenerationChunk | None:
        result = super()._convert_chunk_to_generation_chunk(
            chunk, default_chunk_class, base_generation_info
        )
        if result is None or not isinstance(result.message, AIMessageChunk):
            return result

        choices = chunk.get("choices") or chunk.get("chunk", {}).get("choices") or []
        delta = choices[0].get("delta") if choices and isinstance(choices[0], dict) else None
        if not isinstance(delta, dict):
            return result

        # Deliberately use only a field *actually returned* by the gateway.
        for field in ("reasoning_content", "reasoning"):
            value = delta.get(field)
            if isinstance(value, str) and value:
                result.message.additional_kwargs["reasoning_content"] = value
                break
        return result


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
        if not virtual_key:
            raise BifrostConfigurationError(
                "BIFROST_VIRTUAL_KEY is not configured. For the managed "
                "gateway, run pnpm trajecta:install; for an external "
                "gateway, create a virtual key in Bifrost and set the environment variable."
            )

        model = self.canonical_model_name(
            model_name
        )

        stripped = bifrost_url.rstrip("/")

        base_url = (
            stripped
            if stripped.endswith("/v1")
            else f"{stripped}/v1"
        )

        return BifrostReasoningChatOpenAI(
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
        return os.environ.get("BIFROST_VIRTUAL_KEY", "").strip()

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
        """Preserve the exact ID selected by the user.

        Qualified IDs pin an upstream provider. Bare IDs may be resolved by
        Bifrost's model catalog or virtual-key routing rules. Never attach
        Trajecta's default provider to a bare ID.
        """
        model = (
            model_name
            or self._settings.chat.default_model
        ).strip()

        if not model:
            raise BifrostConfigurationError(
                "Model cannot be empty"
            )

        return model

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
        provider = canonical.split("/", 1)[0] if "/" in canonical else None
        known = await self._known_models(provider)
        # If Bifrost model discovery itself is unavailable,
        # let the actual inference request determine whether
        # the model can be used.
        if known is None:
            return canonical
        # A bare name can also be resolved by a Bifrost routing rule that
        # does not appear in the catalog. Let Bifrost validate at inference.
        if provider is not None and canonical not in known:
            raise BifrostConfigurationError(
                f"Selected model {canonical!r} is not in Bifrost's model "
                "catalog. Refresh the catalog or configure routing in Bifrost."
            )
        return canonical

    async def _known_models(
        self,
        provider: str | None,
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
                    params={"provider": provider} if provider else None,
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
                    if provider and "/" not in model_id:
                        model_id = f"{provider}/{model_id}"
                    known.add(model_id)
                return known
        except Exception:
            return None
