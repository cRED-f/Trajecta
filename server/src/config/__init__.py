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


class VectorStoreConfig(BaseModel):
    enabled: bool = True
    mode: str = "embedded"  # "embedded" = in-process qdrant; "server" = remote
    path: str = ".trajecta/data/qdrant"
    url: str | None = None  # when mode == "server"
    collection_prefix: str = "trajecta_"


class MemoryConfig(BaseModel):
    db_path: str = ".trajecta/data/trajecta.db"         # our task/skill/config store + FTS
    langgraph_db_path: str = ".trajecta/data/langgraph.db"  # LangGraph checkpointer + store (one file)
    langgraph_server_url: str = "http://127.0.0.1:2024"
    memory_files: list[str] = Field(default_factory=lambda: ["/memories/AGENTS.md"])
    skills_path: str = "/skills/"
    vector_store: VectorStoreConfig = VectorStoreConfig()


class LlmProviderConfig(BaseModel):
    enabled: bool = True
    model: str = ""
    base_url: str | None = None
    api_key_env: str | None = None  # env var name to pull the key from
    model_env: str | None = None  # env var name to pull the default model from (overrides `model`)


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
    memory: MemoryConfig = MemoryConfig()

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
        """Load the full configuration: defaults, then local overrides, then env vars.

        Merges YAML dicts recursively so a local override of one key under a
        section (e.g. `llm.providers.bifrost.base_url`) doesn't clobber the
        whole section, then validates once.
        """
        data = cls.from_yaml(cls().yaml_defaults_path).model_dump()
        if cls().local_config_path.exists():
            local = cls.from_yaml(cls().local_config_path).model_dump()
            _deep_merge(data, local)
        return cls.model_validate(data)


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> None:
    """Recursively merge `override` into `base` (in place). Lists are replaced."""
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value