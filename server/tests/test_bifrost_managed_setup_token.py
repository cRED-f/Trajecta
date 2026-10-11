"""The auto-installed Bifrost setup token must never leave local port 8080."""
import asyncio
from types import SimpleNamespace

# Import this narrow client without importing optional LLM SDKs from the
# llm_gateway package initializer.
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "bifrost_admin_standalone",
    Path(__file__).resolve().parents[1] / "src" / "llm_gateway" / "bifrost_admin.py",
)
bifrost_admin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bifrost_admin)


def test_management_token_sent_only_to_local_bifrost(monkeypatch):
    calls = []
    token = "a" * 64
    monkeypatch.setenv("TRAJECTA_BIFROST_SETUP_TOKEN", token)

    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        async def request(self, method, url, **kwargs):
            calls.append((method, url, kwargs.get("headers")))
            return SimpleNamespace(status_code=200, json=lambda: {"status": "ok"})

    monkeypatch.setattr(bifrost_admin.httpx, "AsyncClient", Client)
    for host in (
        "http://127.0.0.1:8080", "http://localhost:8080",
        "http://127.0.0.1:9090", "https://remote.example:8080",
    ):
        result = asyncio.run(bifrost_admin.BifrostAdminClient(host)._request("GET", "/api/providers"))
        assert result[0] == 200

    assert calls[0][2] == {"X-Bifrost-Setup-Token": token}
    assert calls[1][2] == {"X-Bifrost-Setup-Token": token}
    assert calls[2][2] == {}
    assert calls[3][2] == {}


def test_invalid_setup_token_is_not_sent(monkeypatch):
    monkeypatch.setenv("TRAJECTA_BIFROST_SETUP_TOKEN", "invalid")
    calls = []

    class Client:
        def __init__(self, **_kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_args): pass
        async def request(self, _method, _url, **kwargs):
            calls.append(kwargs["headers"])
            return SimpleNamespace(status_code=200, json=lambda: {})

    monkeypatch.setattr(bifrost_admin.httpx, "AsyncClient", Client)
    asyncio.run(bifrost_admin.BifrostAdminClient("http://127.0.0.1:8080")._request("GET", "/health"))
    assert calls == [{}]
