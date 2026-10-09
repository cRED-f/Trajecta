"""HTTP contract coverage for the desktop learning and memory console.

Load only the required FastAPI routers; importing the full application would
require the optional agent runtime and a running Bifrost gateway.
"""
from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2] / "src" / "api" / "routes"


def load_route(name: str):
    spec = importlib.util.spec_from_file_location(f"test_console_{name}", ROOT / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.router


class FakeReflection:
    def __init__(self):
        self.current = {
            "enabled": True, "model": None, "max_daily_reviews": 20,
            "max_output_tokens": 500, "timeout_seconds": 45,
        }

    async def get_config(self):
        return dict(self.current)

    async def update_config(self, patch):
        self.current.update(patch)
        return dict(self.current)

    async def status(self, limit=20):
        return {"enabled": self.current["enabled"], "counts": {"completed": 2}, "jobs": []}


class FakeEpisodic:
    def __init__(self):
        self.last_scope = None

    async def list(self, *, scope, **_kwargs):
        self.last_scope = scope
        return [{"id": "ep", "scope": scope}]

    async def get(self, id, *, scope, **_kwargs):
        self.last_scope = scope
        return {"id": id} if scope == "local" else None

    async def delete(self, id, *, scope, **_kwargs):
        self.last_scope = scope
        return scope == "local"


@pytest.fixture
def api(monkeypatch):
    # Avoid the eager import of unrelated sandbox / LLM routers.
    embeddings = ModuleType("server.src.memory.embeddings")
    embeddings.OllamaEmbeddingCatalogService = object
    episodic_module = ModuleType("server.src.memory.episodic.store")
    episodic_module.EpisodicMemory = type("EpisodicMemory", (), {
        "workspace_scope": staticmethod(lambda path: "workspace:" + hashlib.sha256(path.encode()).hexdigest()[:24] if path else "local")
    })
    monkeypatch.setitem(sys.modules, "server.src.memory.embeddings", embeddings)
    monkeypatch.setitem(sys.modules, "server.src.memory.episodic.store", episodic_module)
    app = FastAPI()
    app.state.reflection_worker = FakeReflection()
    app.state.memory_provider = SimpleNamespace(sqlite=object(), episodic=FakeEpisodic())
    app.include_router(load_route("learning"), prefix="/api/v1")
    app.include_router(load_route("memory"), prefix="/api/v1")
    return TestClient(app), app


def test_reflection_settings_round_trip_and_validation(api):
    client, app = api
    assert client.get("/api/v1/learning/reflection/settings").json()["model"] is None
    response = client.patch("/api/v1/learning/reflection/settings", json={
        "model": "ollama/qwen3:8b", "max_daily_reviews": 5, "enabled": False,
    })
    assert response.status_code == 200
    assert response.json()["model"] == "ollama/qwen3:8b"
    assert app.state.reflection_worker.current["enabled"] is False
    assert client.patch("/api/v1/learning/reflection/settings", json={"model": "bad-model"}).status_code == 400
    assert client.patch("/api/v1/learning/reflection/settings", json={"max_daily_reviews": -2}).status_code == 422
    assert client.patch("/api/v1/learning/reflection/settings", json={"enabled": None}).status_code == 422
    assert client.patch("/api/v1/learning/reflection/settings", json={"model": None}).status_code == 200
    assert client.get("/api/v1/learning/reflection/status").json()["counts"]["completed"] == 2


def test_episode_listing_uses_exact_workspace_scope(api):
    client, app = api
    response = client.get("/api/v1/memory/episodic", params={"workspace_path": "/project/one"})
    assert response.status_code == 200
    expected = "workspace:" + hashlib.sha256(b"/project/one").hexdigest()[:24]
    assert response.json()[0]["scope"] == expected
    assert app.state.memory_provider.episodic.last_scope == expected
    assert client.get("/api/v1/memory/episodic/ep", params={"workspace_path": "/project/one"}).status_code == 404
    assert client.get("/api/v1/memory/episodic/ep").status_code == 200
    assert client.delete("/api/v1/memory/episodic/ep", params={"workspace_path": "/project/one"}).status_code == 404
