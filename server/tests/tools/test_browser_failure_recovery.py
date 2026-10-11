"""Regression checks for closed browser sessions, nonfatal errors and no duplicate actions.

Load the focused modules without the optional Deep Agents dependency. Full
agent/middleware integration is exercised in environments with deepagents.
"""
from __future__ import annotations

import importlib
import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from server.src.config import Settings


@pytest.fixture
def modules(monkeypatch):
    directory = Path(__file__).resolve().parents[2] / "src" / "tools" / "personal"
    package_name = "server.src.tools.personal"
    package = types.ModuleType(package_name)
    package.__path__ = [str(directory)]
    monkeypatch.setitem(sys.modules, package_name, package)

    modules = []
    for module_name in ("documents", "browser_recovery", "browser"):
        fullname = f"{package_name}.{module_name}"
        spec = importlib.util.spec_from_file_location(fullname, directory / f"{module_name}.py")
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, fullname, module)
        spec.loader.exec_module(module)
        modules.append(module)
    return modules[1], modules[2]


@pytest.mark.asyncio
async def test_navigate_recovers_once_from_closed_session(modules):
    recovery, _browser_module = modules
    browser = Mock()
    browser.reset = AsyncMock()
    closed = PlaywrightError("Page.goto: Target page, context or browser has been closed")
    browser.navigate = AsyncMock(side_effect=[closed, {"url": "https://example.edu", "title": "OK"}])

    response = await recovery.call_browser_tool(
        browser, "browser_navigate",
        lambda: browser.navigate("https://example.edu"),
        retry_navigation_on_closed=True,
    )
    assert response["url"] == "https://example.edu"
    assert browser.navigate.await_count == 2
    browser.reset.assert_awaited_once()


@pytest.mark.asyncio
async def test_closed_session_does_not_replay_submit_or_click(modules):
    recovery, _browser_module = modules
    browser = Mock()
    browser.reset = AsyncMock()
    browser.click = AsyncMock(side_effect=PlaywrightError(
        "Locator.click: Target page, context or browser has been closed"
    ))

    result = await recovery.call_browser_tool(
        browser, "browser_click", lambda: browser.click("t-0")
    )
    assert result["ok"] is False
    assert result["recoverable"] is True
    assert result["session_restarted"] is True
    assert result["reason"] == "browser_session_closed"
    assert browser.click.await_count == 1
    browser.reset.assert_awaited_once()
    assert "Do not automatically replay" in result["message"]


@pytest.mark.asyncio
async def test_browser_timeout_returns_nonfatal_observation_without_reset(modules):
    recovery, _browser_module = modules
    browser = Mock()
    browser.reset = AsyncMock()
    browser.navigate = AsyncMock(side_effect=PlaywrightTimeoutError("Page.goto: Timeout 30000ms"))

    response = await recovery.call_browser_tool(
        browser, "browser_navigate", lambda: browser.navigate("https://slow.edu"),
        retry_navigation_on_closed=True,
    )
    assert response["ok"] is False
    assert response["reason"] == "browser_timeout"
    browser.reset.assert_not_awaited()
    assert browser.navigate.await_count == 1


@pytest.mark.asyncio
async def test_non_browser_permission_failure_is_not_swallowed(modules):
    recovery, _browser_module = modules
    browser = Mock()
    browser.reset = AsyncMock()
    browser.download = AsyncMock(side_effect=PermissionError("disallowed"))

    with pytest.raises(PermissionError):
        await recovery.call_browser_tool(browser, "browser_download", lambda: browser.download("t-0"))
    browser.reset.assert_not_awaited()


@pytest.mark.asyncio
async def test_second_navigation_failure_is_bounded(modules):
    recovery, _browser_module = modules
    browser = Mock()
    browser.reset = AsyncMock()
    browser.navigate = AsyncMock(side_effect=[
        PlaywrightError("Target page, context or browser has been closed"),
        PlaywrightError("Target page, context or browser has been closed"),
    ])
    result = await recovery.call_browser_tool(
        browser, "browser_navigate", lambda: browser.navigate("https://example.edu"),
        retry_navigation_on_closed=True,
    )
    assert result["ok"] is False
    assert browser.navigate.await_count == 2
    assert browser.reset.await_count == 1


@pytest.mark.asyncio
async def test_cached_closed_page_is_rebuilt(modules):
    _recovery, browser_module = modules
    settings = Settings()
    manager = browser_module.BrowserManager(settings)
    closed_page = Mock()
    closed_page.is_closed.return_value = True
    manager._page = closed_page
    manager._browser = Mock()
    manager._browser.is_connected.return_value = True
    manager._context = Mock()
    manager.reset = AsyncMock()

    # Simulate a new driver start without launching an actual browser.
    new_page = Mock()
    new_page.is_closed.return_value = False
    new_page.set_default_timeout = Mock()
    fake_context = Mock()
    fake_context.new_page = AsyncMock(return_value=new_page)
    fake_browser = Mock()
    fake_browser.new_context = AsyncMock(return_value=fake_context)
    fake_type = Mock()
    fake_type.launch = AsyncMock(return_value=fake_browser)
    fake_playwright = Mock()
    fake_playwright.chromium = fake_type
    import playwright.async_api as playwright_api
    original = playwright_api.async_playwright
    playwright_api.async_playwright = lambda: Mock(start=AsyncMock(return_value=fake_playwright))
    try:
        result = await manager._ensure()
    finally:
        playwright_api.async_playwright = original
    assert result is new_page
    manager.reset.assert_awaited_once()


@pytest.mark.asyncio
async def test_submit_and_verify_never_double_clicks_when_spa_updates(modules):
    _recovery, browser_module = modules
    manager = browser_module.BrowserManager(Settings())
    page = Mock()
    page.url = "https://example.edu/form"
    page.title = AsyncMock(return_value="Submitted")
    page.wait_for_timeout = AsyncMock()
    element = Mock()
    element.click = AsyncMock()
    body = Mock()
    body.inner_text = AsyncMock(return_value="Thank you")
    page.locator.side_effect = lambda selector: body if selector == "body" else element
    manager._ensure = AsyncMock(return_value=page)

    outcome = await manager.submit_and_verify("t-1", expected_text="Thank you")
    assert outcome["ok"] is True
    element.click.assert_awaited_once()
