"""Application configuration — loads YAML defaults overridden by environment variables and local config."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ServerConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8420


class ChatRagConfig(BaseModel):
    enabled: bool = True
    chunk_size_chars: int = 3_000
    chunk_overlap_chars: int = 400
    auto_index_min_chars: int = 4_000
    top_k: int = 8
    max_tool_chars: int = 24_000


class ChatConfig(BaseModel):
    uploads_path: str = ".trajecta/uploads"

    # Desktop/local-agent default.
    default_model: str = "openai/gpt-4o-mini"

    max_upload_mb: int = 100
    max_extracted_chars: int = 2_000_000

    stream_heartbeat_seconds: float = 15.0
    rag: ChatRagConfig = ChatRagConfig()


class WebToolsConfig(BaseModel):
    enabled: bool = True
    # Optional self-hosted SearXNG endpoint. When unset, `web_search` falls back
    # to DuckDuckGo's public HTML endpoint (best-effort, no API key).
    searxng_url: str | None = None
    request_timeout_seconds: float = 20.0
    max_response_bytes: int = 2_000_000
    user_agent: str = "Trajecta/0.1 (+local-first personal agent)"


class BrowserToolsConfig(BaseModel):
    enabled: bool = True
    headless: bool = False
    browser: str = "chromium"
    default_timeout_ms: int = 20_000
    downloads_path: str = ".trajecta/downloads"


class ComputerToolsConfig(BaseModel):
    # Desktop control is intentionally opt-in because it can act outside the
    # browser and outside Deep Agents' filesystem permission boundary.
    enabled: bool = False
    screenshot_path: str = ".trajecta/screenshots"


class SchedulerToolsConfig(BaseModel):
    enabled: bool = True
    poll_seconds: float = 5.0


class ReadbackComparisonConfig(BaseModel):
    """One equality/contains/existence check between requested and actual state."""

    actual_path: str
    expected_from: str | None = None
    expected_value: Any | None = None
    operator: Literal["equals", "contains", "exists", "not_exists"] = "equals"
    required: bool = True


class ConnectorVerificationRuleConfig(BaseModel):
    """One mutating connector action and the independent tool used to verify the resulting state."""

    action_tool: str

    # Usually the same server as the action's server.
    readback_tool: str | None = None
    readback_server: str | None = None

    # Try these result paths until a resource identity is found.
    resource_id_paths: list[str] = Field(
        default_factory=lambda: [
            "id",
            "resource.id",
            "message.id",
            "event.id",
            "task.id",
            "data.id",
        ]
    )

    # Arguments passed to the readback tool. Values beginning with "$" are
    # expressions: $receipt.resource_id, $action.args.calendar_id, $action.result.id
    readback_args: dict[str, Any] = Field(default_factory=dict)

    comparisons: list[ReadbackComparisonConfig] = Field(default_factory=list)

    verification_required: bool = True

    manual_only: bool = False


class HttpVerificationRuleConfig(BaseModel):
    method: str
    url_regex: str

    # If the response contains an ID.
    resource_id_paths: list[str] = Field(
        default_factory=lambda: [
            "json.id",
            "json.data.id",
            "json.resource.id",
        ]
    )

    # May contain {resource_id}.
    readback_url_template: str | None = None
    readback_method: str = "GET"

    comparisons: list[ReadbackComparisonConfig] = Field(default_factory=list)


class ConnectorVerificationConfig(BaseModel):
    enabled: bool = True

    # Fail closed means a mutating operation configured for verification
    # cannot be reported as successful if readback fails.
    fail_closed: bool = True

    rules: dict[str, ConnectorVerificationRuleConfig] = Field(default_factory=dict)
    http_rules: dict[str, HttpVerificationRuleConfig] = Field(default_factory=dict)


class ToolsConfig(BaseModel):
    # FastMCP / LangChain MCPConfig shape.
    mcp_servers: dict[str, dict[str, Any]] = Field(default_factory=dict)
    discovery_timeout_seconds: float = 15.0

    # Host files explicitly exposed at /workspace/ through Deep Agents'
    # FilesystemBackend. `virtual_mode=True` prevents path escape.
    workspace_root: str = "."

    web: WebToolsConfig = WebToolsConfig()
    browser: BrowserToolsConfig = BrowserToolsConfig()
    computer: ComputerToolsConfig = ComputerToolsConfig()
    scheduler: SchedulerToolsConfig = SchedulerToolsConfig()

    connector_verification: ConnectorVerificationConfig = ConnectorVerificationConfig()


class SkillFixtureConfig(BaseModel):
    enabled: bool = True
    root: str = ".trajecta/data/replay-fixtures"
    capture_workspace: bool = True
    max_files: int = 1000
    max_file_mb: int = 8
    max_total_mb: int = 100
    exclude_globs: list[str] = Field(
        default_factory=lambda: [
            ".git/**",
            ".trajecta/**",
            "node_modules/**",
            ".venv/**",
            "venv/**",
            "__pycache__/**",
            ".pytest_cache/**",
            ".mypy_cache/**",
            ".ruff_cache/**",
            "dist/**",
            "build/**",
        ]
    )


class SkillsConfig(BaseModel):
    storage_path: str = ".trajecta/skills"
    fixtures: SkillFixtureConfig = SkillFixtureConfig()


class SandboxConfig(BaseModel):
    enabled: bool = True
    image: str = "trajecta-sandbox:latest"
    timeout_seconds: int = 300
    memory_limit: str = "512m"
    cpu_limit: float = 1.0
    network_enabled: bool = False
    auto_remove: bool = True


class GuardrailsConfig(BaseModel):
    default_risk_level: str = "SENSITIVE"
    rules_path: str = "config/guardrail-rules.yaml"
    # Deep Agents HITL interrupts require a client resume flow. Keep this
    # configurable so non-interactive/headless runs can still operate.
    hitl_enabled: bool = True


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

    chat: ChatConfig = ChatConfig()
    tools: ToolsConfig = ToolsConfig()
    skills: SkillsConfig = SkillsConfig()
    sandbox: SandboxConfig = SandboxConfig()
    guardrails: GuardrailsConfig = GuardrailsConfig()

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