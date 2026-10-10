from __future__ import annotations

import json

import httpx

from server.src.llm_gateway.bifrost_admin import (
    BifrostAdminClient,
    classify_provider_type,
    key_status_ok,
)


def _patch_transport(
    monkeypatch,
    handler,
) -> None:
    """Route the client's httpx calls through a scripted transport."""
    real_client = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)


def test_classify_provider_type() -> None:
    assert classify_provider_type("openai") == "openai"
    assert classify_provider_type("anthropic") == "anthropic"
    assert classify_provider_type("ollama") == "ollama"
    assert classify_provider_type("9router") == "openai_compat"
    assert classify_provider_type("myrelay") == "openai_compat"


def test_key_status_ok_only_rejects_explicit_failures() -> None:
    assert key_status_ok({"status": "success"})
    assert key_status_ok({"status": ""})
    assert key_status_ok({"status": "unknown"})
    assert key_status_ok({})
    assert not key_status_ok({"status": "list_models_failed"})
    assert not key_status_ok({"status": "error"})


async def test_provider_models_prefixes_bare_names(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/models"
        assert request.url.params["provider"] == "9router"
        return httpx.Response(
            200,
            json={
                "models": [
                    {"name": "af/gpt-oss-120b", "provider": "9router"},
                    {"name": "9router/already-prefixed"},
                ],
                "total": 2,
            },
        )

    _patch_transport(monkeypatch, handler)
    admin = BifrostAdminClient("http://bifrost.test")

    models = await admin.provider_models("9router")

    assert models == [
        "9router/af/gpt-oss-120b",
        "9router/already-prefixed",
    ]


async def test_upsert_creates_provider_then_key(monkeypatch) -> None:
    """POST the provider first, then PUT the full network config, then keys."""
    state = {"created": False}
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        method = request.method
        path = request.url.path
        calls.append((method, path))

        if (method, path) == ("GET", "/api/providers/myrelay"):
            if not state["created"]:
                return httpx.Response(404, json={"error": "not found"})
            return httpx.Response(
                200,
                json={
                    "name": "myrelay",
                    "network_config": {
                        "base_url": "http://old.example:9000",
                        "allow_private_network": True,
                    },
                    "concurrency_and_buffer_size": {
                        "concurrency": 1000,
                        "buffer_size": 5000,
                    },
                },
            )

        if (method, path) == ("POST", "/api/providers"):
            body = json.loads(request.content)
            state["created"] = True
            assert body["provider"] == "myrelay"
            assert body["network_config"]["base_url"] == (
                "http://new.example:9000"
            )
            assert body["network_config"]["allow_private_network"] is True
            assert body["custom_provider_config"] == {
                "base_provider_type": "openai"
            }
            return httpx.Response(201, json={"name": "myrelay"})

        if (method, path) == ("PUT", "/api/providers/myrelay"):
            body = json.loads(request.content)
            # PUT must carry a complete network_config and a valid
            # concurrency block, otherwise Bifrost rejects or wipes them.
            assert body["network_config"]["base_url"] == (
                "http://new.example:9000"
            )
            assert body["network_config"]["allow_private_network"] is True
            concurrency = body["concurrency_and_buffer_size"]
            assert concurrency["concurrency"] == 1000
            assert concurrency["buffer_size"] == 5000
            assert "keys" not in body
            return httpx.Response(200, json={"name": "myrelay"})

        if (method, path) == ("GET", "/api/providers/myrelay/keys"):
            return httpx.Response(200, json={"keys": [], "total": 0})

        if (method, path) == ("POST", "/api/providers/myrelay/keys"):
            body = json.loads(request.content)
            assert body["value"] == "sk-new"
            assert body["models"] == ["*"]
            return httpx.Response(201, json={"id": "k1"})

        return httpx.Response(
            404,
            json={"error": f"no route {method} {path}"},
        )

    _patch_transport(monkeypatch, handler)
    admin = BifrostAdminClient("http://bifrost.test")

    await admin.upsert_provider(
        provider="myrelay",
        provider_type="openai_compat",
        base_url="http://new.example:9000",
        api_key="sk-new",
    )

    assert (
        "POST",
        "/api/providers",
    ) in calls
    assert (
        "PUT",
        "/api/providers/myrelay",
    ) in calls
    assert (
        "POST",
        "/api/providers/myrelay/keys",
    ) in calls


async def test_upsert_without_a_key_leaves_existing_keys_alone(
    monkeypatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        method, path = request.method, request.url.path

        if (method, path) == ("GET", "/api/providers/9router"):
            return httpx.Response(
                200,
                json={
                    "name": "9router",
                    "network_config": {
                        "base_url": "http://127.0.0.1:20128",
                        "allow_private_network": True,
                        "extra_headers": {"Accept": "application/json"},
                    },
                    "concurrency_and_buffer_size": {
                        "concurrency": 1000,
                        "buffer_size": 5000,
                    },
                },
            )

        if (method, path) == ("PUT", "/api/providers/9router"):
            body = json.loads(request.content)
            # Stored extras survive a base_url-less update.
            assert body["network_config"]["extra_headers"] == {
                "Accept": "application/json"
            }
            assert body["network_config"]["base_url"] == (
                "http://127.0.0.1:20128"
            )
            return httpx.Response(200, json={"name": "9router"})

        if (method, path) == ("GET", "/api/providers/9router/keys"):
            return httpx.Response(
                200,
                json={
                    "keys": [
                        {
                            "id": "existing-key",
                            "value": {"type": "env"},
                            "status": "success",
                        }
                    ],
                    "total": 1,
                },
            )

        return httpx.Response(
            404,
            json={"error": f"no route {method} {path}"},
        )

    _patch_transport(monkeypatch, handler)
    admin = BifrostAdminClient("http://bifrost.test")

    await admin.upsert_provider(
        provider="9router",
        provider_type="openai_compat",
    )

    # No POST/PUT to the keys endpoints happened (the GET above is the
    # only keys call the handler allows, otherwise it would 404).


async def test_ollama_key_receives_url(monkeypatch) -> None:
    """Bifrost 400s on Ollama keys that lack ollama_key_config.url."""
    provider_bodies: list[dict] = []
    key_bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        method, path = request.method, request.url.path

        if (method, path) == ("GET", "/api/providers/ollama"):
            return httpx.Response(
                200,
                json={
                    "name": "ollama",
                    "network_config": {
                        "base_url": "http://localhost:11434",
                        "allow_private_network": True,
                    },
                    "concurrency_and_buffer_size": {
                        "concurrency": 1000,
                        "buffer_size": 5000,
                    },
                },
            )

        if (method, path) == ("PUT", "/api/providers/ollama"):
            provider_bodies.append(json.loads(request.content))
            return httpx.Response(200, json={"name": "ollama"})

        if (method, path) == ("GET", "/api/providers/ollama/keys"):
            return httpx.Response(200, json={"keys": [], "total": 0})

        if (method, path) == ("POST", "/api/providers/ollama/keys"):
            key_bodies.append(json.loads(request.content))
            return httpx.Response(201, json={"id": "k1"})

        return httpx.Response(
            404,
            json={"error": f"no route {method} {path}"},
        )

    _patch_transport(monkeypatch, handler)
    admin = BifrostAdminClient("http://bifrost.test")

    # No base_url from the caller — the native default applies to
    # both the provider's network config and the key itself.
    await admin.upsert_provider(provider="ollama", provider_type="ollama")

    assert provider_bodies[0]["network_config"]["base_url"] == (
        "http://127.0.0.1:11434"
    )
    assert key_bodies == [
        {
            "name": "ollama-local",
            "value": "",
            "models": ["*"],
            "weight": 1.0,
            "ollama_key_config": {
                "url": "http://127.0.0.1:11434"
            },
        }
    ]


async def test_ollama_key_url_is_backfilled_when_stale(
    monkeypatch,
) -> None:
    """An existing key keeps working — its URL is corrected in place."""
    state = {
        "url": "http://localhost:11434",
        "id": "k1",
    }
    key_writes: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        method, path = request.method, request.url.path

        if (method, path) == ("GET", "/api/providers/ollama"):
            return httpx.Response(
                200,
                json={
                    "name": "ollama",
                    "network_config": {
                        "base_url": state["url"],
                        "allow_private_network": True,
                    },
                    "concurrency_and_buffer_size": {
                        "concurrency": 1000,
                        "buffer_size": 5000,
                    },
                },
            )

        if (method, path) == ("PUT", "/api/providers/ollama"):
            return httpx.Response(200, json={"name": "ollama"})

        if (method, path) == ("GET", "/api/providers/ollama/keys"):
            return httpx.Response(
                200,
                json={
                    "keys": [
                        {
                            "id": state["id"],
                            "name": "ollama-local",
                            "value": {"value": "", "type": "plain_text"},
                            "models": ["*"],
                            "weight": 1.0,
                            "ollama_key_config": {
                                "url": {
                                    "value": state["url"],
                                    "type": "plain_text",
                                }
                            },
                            "status": "success",
                        }
                    ],
                    "total": 1,
                },
            )

        if method == "PUT" and path.startswith(
            "/api/providers/ollama/keys/"
        ):
            body = json.loads(request.content)
            key_writes.append((path, body))
            state["url"] = body["ollama_key_config"]["url"]
            return httpx.Response(200, json={"id": state["id"]})

        if (method, path) == ("POST", "/api/providers/ollama/keys"):
            raise AssertionError("an existing key must be updated, not added")

        return httpx.Response(
            404,
            json={"error": f"no route {method} {path}"},
        )

    _patch_transport(monkeypatch, handler)
    admin = BifrostAdminClient("http://bifrost.test")

    await admin.upsert_provider(provider="ollama", provider_type="ollama")

    # A key write is a replace, not a merge — models and weight have to
    # come back with it or they reset and routing stops matching.
    assert key_writes == [
        (
            "/api/providers/ollama/keys/k1",
            {
                "name": "ollama-local",
                "value": "",
                "models": ["*"],
                "weight": 1.0,
                "ollama_key_config": {
                    "url": "http://127.0.0.1:11434"
                },
            },
        )
    ]

    # Re-saving with the URL already correct writes nothing.
    await admin.upsert_provider(provider="ollama", provider_type="ollama")
    assert len(key_writes) == 1


async def test_test_provider_without_keys_is_not_reachable(
    monkeypatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/api/providers/ollama/keys"
        return httpx.Response(200, json={"keys": [], "total": 0})

    _patch_transport(monkeypatch, handler)
    admin = BifrostAdminClient("http://bifrost.test")

    result = await admin.test_provider("ollama")

    assert result["reachable"] is False
    assert result["status"] == "no_key"


async def test_test_provider_refreshes_and_reads_key_status(
    monkeypatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        method, path = request.method, request.url.path

        if (method, path) == ("GET", "/api/providers/9router/keys"):
            return httpx.Response(
                200,
                json={
                    "keys": [{"id": "k", "status": "success"}],
                    "total": 1,
                },
            )

        if (method, path) == (
            "POST",
            "/api/providers/9router/refresh-models",
        ):
            return httpx.Response(
                200,
                json={
                    "keys": [
                        {"id": "k", "status": "list_models_failed"}
                    ],
                    "total": 1,
                },
            )

        return httpx.Response(
            404,
            json={"error": f"no route {method} {path}"},
        )

    _patch_transport(monkeypatch, handler)
    admin = BifrostAdminClient("http://bifrost.test")

    result = await admin.test_provider("9router")

    assert result["reachable"] is False
    assert result["status"] == "failed"


async def test_ensure_allow_all_flips_the_trajecta_key(
    monkeypatch,
) -> None:
    flipped: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        method, path = request.method, request.url.path

        if (method, path) == ("GET", "/api/governance/virtual-keys"):
            return httpx.Response(
                200,
                json={
                    "virtual_keys": [
                        {
                            "id": "vk-trajecta-local",
                            "name": "trajecta-local",
                            "allow_all_providers": False,
                        }
                    ],
                },
            )

        if (method, path) == (
            "PUT",
            "/api/governance/virtual-keys/vk-trajecta-local",
        ):
            flipped.append(json.loads(request.content))
            return httpx.Response(
                200,
                json={"id": "vk-trajecta-local"},
            )

        return httpx.Response(
            404,
            json={"error": f"no route {method} {path}"},
        )

    _patch_transport(monkeypatch, handler)
    admin = BifrostAdminClient("http://bifrost.test")

    await admin.ensure_allow_all_providers()

    assert flipped == [{"allow_all_providers": True}]


async def test_ensure_allow_all_is_a_no_op_when_already_open(
    monkeypatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        return httpx.Response(
            200,
            json={
                "virtual_keys": [
                    {
                        "id": "vk-trajecta-local",
                        "name": "trajecta-local",
                        "allow_all_providers": True,
                    }
                ],
            },
        )

    _patch_transport(monkeypatch, handler)
    admin = BifrostAdminClient("http://bifrost.test")

    await admin.ensure_allow_all_providers()


async def test_health_is_false_when_the_gateway_is_down(
    monkeypatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    _patch_transport(monkeypatch, handler)
    admin = BifrostAdminClient("http://bifrost.test")

    assert await admin.health() is False


async def test_missing_gateway_url_never_raises_on_health() -> None:
    admin = BifrostAdminClient(None)

    assert admin.base_url is None
    assert await admin.health() is False
