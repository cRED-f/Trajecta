"""Regression coverage for recoverable page/search failures.

These tests load network.py directly, because the full personal-tool package
pulls in optional Deep Agents dependencies that this unit does not need.
"""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest

from server.src.config import Settings


@pytest.fixture
def network_module(monkeypatch):
    # Bypass the unrelated verification package's eager dependency imports.
    name = "server.src.tools.verification.values"
    fake = types.ModuleType(name)
    fake.get_path = lambda obj, key: obj.get(key)  # not exercised here
    monkeypatch.setitem(sys.modules, name, fake)
    path = Path(__file__).resolve().parents[2] / "src" / "tools" / "personal" / "network.py"
    spec = importlib.util.spec_from_file_location("isolated_web_network", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


def settings() -> Settings:
    return Settings.model_validate({"tools": {"web": {"request_timeout_seconds": 1}}})


async def attach_transport(network, handler):
    await network._client.aclose()
    await network._alt_client.aclose()
    network._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    network._alt_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_404_finds_updated_official_page_on_same_domain(network_module, monkeypatch):
    net = network_module.NetworkTools(settings())
    calls = []
    original = "https://www.tum.de/en/studies/degree-programs/detail/artificial-intelligence-master-of-science-msc"
    updated = "https://www.tum.de/en/studies/degree-programs/detail/updated-masters"

    def handler(request):
        calls.append(str(request.url))
        if str(request.url) == original:
            return httpx.Response(404, request=request)
        if str(request.url) == updated:
            return httpx.Response(200, text="<html><main>Updated official program information</main></html>", headers={"content-type":"text/html"}, request=request)
        raise AssertionError(f"Unexpected web request: {request.url}")

    try:
        await attach_transport(net, handler)
        net.web_search = AsyncMock(return_value=[{"url": updated, "title":"Updated masters", "snippet":""}])
        monkeypatch.setattr(network_module.NetworkTools, "_host_is_public", AsyncMock(return_value=True))
        result = await net.web_extract(f"[program]({original})")
        assert result["ok"] is True
        assert result["recovered"] is True
        assert result["reason"] == "HTTP 404"
        assert result["original_url"] == original
        assert result["url"] == updated
        assert "Updated official" in result["text"]
        assert calls.count(original) == 1
        assert calls.count(updated) == 1
        assert net.web_search.await_args_list[0].args[0].startswith("site:www.tum.de ")
    finally:
        await net.close()


@pytest.mark.asyncio
async def test_empty_searxng_results_fall_back_to_duckduckgo(network_module):
    cfg = settings()
    cfg.tools.web.searxng_url = "https://searx.test"
    net = network_module.NetworkTools(cfg)
    seen = []

    def handler(request):
        seen.append((request.method, request.url.host))
        if request.url.host == "searx.test":
            return httpx.Response(200, json={"results":[]}, request=request)
        if request.url.host == "html.duckduckgo.com":
            return httpx.Response(200, text='''<div class="result"><a class="result__a" href="https://example.edu/program">Official program</a><div class="result__snippet">Master programme</div></div>''', request=request)
        raise AssertionError(f"Unexpected request {request.url}")

    try:
        await attach_transport(net, handler)
        hits = await net.web_search("masters artificial intelligence")
        assert len(hits) == 1
        assert hits[0]["url"] == "https://example.edu/program"
        assert hits[0]["engine"] == "duckduckgo-html"
        assert seen == [("GET", "searx.test"), ("POST", "html.duckduckgo.com")]
    finally:
        await net.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [403, 404, 429, 500])
async def test_unavailable_sources_return_nonfatal_observation(network_module, monkeypatch, status):
    net = network_module.NetworkTools(settings())
    original = "https://university.edu/master/ai"
    attempts = []

    def handler(request):
        attempts.append(str(request.url))
        return httpx.Response(status if str(request.url) == original else 404, request=request)

    try:
        await attach_transport(net, handler)
        monkeypatch.setattr(network_module.NetworkTools, "_host_is_public", AsyncMock(return_value=True))
        net.web_search = AsyncMock(return_value=[
            {"url":original}, {"url":"https://university.edu/other-page"},
            {"url":"https://independent.edu/masters"},
        ])
        result = await net.web_extract(original)
        assert result["ok"] is False
        assert result["recoverable"] is True
        assert result["reason"] == f"HTTP {status}"
        assert result["alternatives_attempted"] <= 4
        assert attempts.count(original) == 1
    finally:
        await net.close()


@pytest.mark.asyncio
async def test_search_outage_is_nonfatal_and_bounded(network_module):
    net = network_module.NetworkTools(settings())
    original = "https://example.edu/master/artificial-intelligence"

    def handler(request):
        return httpx.Response(404, request=request)

    try:
        await attach_transport(net, handler)
        net.web_search = AsyncMock(side_effect=httpx.ConnectTimeout("search unavailable"))
        result = await net.web_extract(original)
        assert result["ok"] is False
        assert result["recoverable"] is True
        assert result["alternatives_attempted"] == 0
        assert net.web_search.await_count <= 2
        assert "ConnectTimeout" in result["search_error"]
    finally:
        await net.close()


@pytest.mark.asyncio
async def test_existing_page_does_not_use_fallback(network_module):
    net = network_module.NetworkTools(settings())
    def handler(request):
        return httpx.Response(200, text="Original page", request=request)
    try:
        await attach_transport(net, handler)
        net.web_search = AsyncMock()
        result = await net.web_extract("https://university.edu/working")
        assert result["ok"] is True
        assert result["text"] == "Original page"
        net.web_search.assert_not_called()
    finally:
        await net.close()


@pytest.mark.asyncio
async def test_private_alternative_is_rejected(network_module, monkeypatch):
    net = network_module.NetworkTools(settings())
    def handler(request):
        return httpx.Response(404, request=request)
    try:
        await attach_transport(net, handler)
        net.web_search = AsyncMock(return_value=[{"url":"http://localhost:8080/admin"}])
        result = await net.web_extract("https://university.edu/master/ai")
        assert result["ok"] is False
        assert result["alternatives_attempted"] == 0
    finally:
        await net.close()


@pytest.mark.asyncio
async def test_broken_official_results_still_try_independent_search(network_module, monkeypatch):
    net = network_module.NetworkTools(settings())
    original = "https://university.edu/old/ai"
    working = "https://another-university.edu/ai-program"
    queried = []
    seen = []

    def handler(request):
        seen.append(str(request.url))
        if str(request.url) == working:
            return httpx.Response(200, text="Independent AI programme reference", request=request)
        return httpx.Response(404, request=request)

    async def search(query, **_kwargs):
        queried.append(query)
        if query.startswith("site:"):
            return [{"url": f"https://university.edu/dead/{n}"} for n in range(8)]
        return [{"url":working}]

    try:
        await attach_transport(net, handler)
        monkeypatch.setattr(network_module.NetworkTools, "_host_is_public", AsyncMock(return_value=True))
        net.web_search = search
        result = await net.web_extract(original)
        assert result["ok"] is True
        assert result["url"] == working
        assert result["recovered"] is True
        assert len(queried) == 2
        assert seen.count(original) == 1
        assert len(seen) <= 4  # one original, two dead, one good
    finally:
        await net.close()
