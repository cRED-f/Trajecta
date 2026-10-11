"""Startup must never make an inference call to an unselected remote model."""
from __future__ import annotations

import asyncio
from pathlib import Path
import importlib.util
from types import SimpleNamespace


# Load the isolated route module without importing the full agent runtime;
# this also makes the pure startup decision testable in minimal environments.
FILE = Path(__file__).resolve().parents[1] / "src/api/routes/startup.py"
SPEC = importlib.util.spec_from_file_location("startup_route_isolated", FILE)
assert SPEC and SPEC.loader
startup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(startup)


class FakeWorker:
    def __init__(self, *, enabled=True, model=None):
        self.config = {"enabled": enabled, "model": model}

    async def get_config(self):
        return self.config


class FakeModelSettings:
    async def get(self):
        return {"default_model": "9router/claude-opus"}


def app_state(*, embedder="placeholder", embedding_model=None, learning_model=None):
    vector = SimpleNamespace(embedding_provider=embedder, embedding_model=embedding_model)
    memory = SimpleNamespace(vector=vector, ollama_embedding_base_url="http://127.0.0.1:11434")
    return SimpleNamespace(memory_provider=memory, reflection_worker=FakeWorker(model=learning_model),
                           llm_settings=FakeModelSettings())


def test_remote_learning_model_does_not_trigger_inference():
    state = app_state(learning_model="9router/claude-opus")
    result = asyncio.run(startup.warmup_selected_models(SimpleNamespace(app=SimpleNamespace(state=state))))
    assert result["embedding"]["state"] == "skipped"
    assert result["learning"]["state"] == "configured"


def test_only_explicitly_selected_local_models_warm(monkeypatch):
    calls = []

    async def fake_warm(**arguments):
        calls.append(arguments)
        return {"state": "ready", "model": arguments["model"]}

    monkeypatch.setattr(startup, "_warm_local_model", fake_warm)
    state = app_state(embedder="ollama", embedding_model="nomic-embed-text", learning_model="ollama/qwen3:8b")
    result = asyncio.run(startup.warmup_selected_models(SimpleNamespace(app=SimpleNamespace(state=state))))
    assert result["embedding"]["state"] == "ready"
    assert result["learning"]["state"] == "ready"
    assert {entry["model"] for entry in calls} == {"nomic-embed-text", "qwen3:8b"}
