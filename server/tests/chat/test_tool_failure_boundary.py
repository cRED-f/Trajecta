"""Integration-level tests for the LangChain tool-error boundary.

Skipped only on minimal environments that lack Deep Agents / LangChain.
"""
from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

pytest.importorskip("deepagents")

from server.src.chat.runtime import _recoverable_tool_error  # noqa: E402


def tool_call(name: str) -> SimpleNamespace:
    return SimpleNamespace(tool_call={"name": name, "id": "call-1", "args": {}})


def test_unexpected_tool_error_is_nonfatal_and_sanitized() -> None:
    msg = _recoverable_tool_error(RuntimeError("private database password"), tool_call("process_output"))
    assert msg is not None
    assert "RuntimeError" in msg
    assert "private database password" not in msg
    assert "Continue the task" in msg


def test_browser_closed_returns_fallback_instead_of_fatal_error() -> None:
    from playwright.async_api import Error as PlaywrightError
    msg = _recoverable_tool_error(
        PlaywrightError("Page.goto: Target page, context or browser has been closed"),
        tool_call("browser_navigate"),
    )
    assert msg is not None
    assert "web_search/web_extract" in msg
    assert "browser_session_closed" in msg


def test_web_timeout_produces_alternative_search_advice() -> None:
    msg = _recoverable_tool_error(httpx.ConnectTimeout("upstream unavailable"), tool_call("web_search"))
    assert msg is not None
    assert "Search for another source" in msg


def test_permission_errors_do_not_bypass_policy() -> None:
    assert _recoverable_tool_error(PermissionError("access denied"), tool_call("write_file")) is None
