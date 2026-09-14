"""Application configuration — loads YAML defaults overridden by environment variables and local config."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ServerConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8420


class LlmProviderConfig(BaseModel):
    enabled: bool = True
    model: str = ""
    base_url: str | None = None


class LlmConfig(BaseModel):
    default_provider: str = "openai"
    providers: dict[str, LlmProviderConfig] = Field(
        default_factory=lambda: {"openai": LlmProviderConfig(model="openai:gpt-5.5")}
    )


class Settings(BaseSettings):
    """Global server settings.

    Resolution order: YAML defaults < local config file < environment variables.
    """

    model_config = SettingsConfigDict(
        env_prefix="TRAJECTA_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    server: ServerConfig = ServerConfig()
    llm: LlmConfig = LlmConfig()

    yaml_defaults_path: Path = Path("config/default.yaml")
    local_config_path: Path = Path(".trajecta/config.yaml")

    @classmethod
    def from_yaml(cls, path: Path | str) -> "Settings":
        """Load settings from a YAML file into the pydantic model."""
        path = Path(path)
        if not path.exists():
            return cls()
        data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return cls.model_validate(data)

    @classmethod
    def load(cls) -> "Settings":
        """Load the full configuration: defaults, then local overrides enforced by env vars."""
        settings = cls.from_yaml(cls().yaml_defaults_path)
        if settings.local_config_path.exists():
            local = cls.from_yaml(settings.local_config_path)
        else:
            local = cls()
        return settings.model_copy(update=local.model_dump())