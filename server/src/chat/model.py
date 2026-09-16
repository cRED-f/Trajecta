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
        bifrost_url = (
            os.environ.get(
                "TRAJECTA_BIFROST_URL"
            )
            or self._configured_url()
        )

        if not bifrost_url:
            raise BifrostConfigurationError(
                "Trajecta chat requires Bifrost. "
                "Set TRAJECTA_BIFROST_URL or enable "
                "llm.providers.bifrost.base_url."
            )

        virtual_key = os.environ.get(
            "BIFROST_VIRTUAL_KEY",
            "sk-bf-local",
        )

        model = (
            model_name
            or self._settings.chat.default_model
        )

        base_url = (
            f"{bifrost_url.rstrip('/')}"
            "/langchain"
        )

        return ChatOpenAI(
            model=model,
            base_url=base_url,
            api_key=virtual_key,

            # Let Bifrost own retries/fallback behavior.
            max_retries=0,

            timeout=180,
        )

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
