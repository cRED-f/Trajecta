"""Bounded, nonfatal recovery for Playwright tool failures.

Only safe page navigation is retried after a browser session disappears.
Element actions are never repeated: a click/submit/upload may already have
committed a remote change before the browser lost its connection.
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

try:
    from playwright.async_api import Error as PlaywrightError
    from playwright.async_api import TimeoutError as PlaywrightTimeoutError
except ImportError:  # Browser tools can be disabled without Playwright installed.
    class PlaywrightError(Exception):
        pass

    class PlaywrightTimeoutError(PlaywrightError):
        pass

logger = logging.getLogger(__name__)

_CLOSED_MESSAGES = (
    "target page, context or browser has been closed",
    "target closed",
    "browser has been closed",
    "browser closed",
    "context closed",
    "page has been closed",
    "connection closed",
    "browser disconnected",
)


def is_browser_closed(exc: Exception) -> bool:
    """A closed-session signature, not an arbitrary exception mentioning 'closed'."""
    return isinstance(exc, PlaywrightError) and any(
        text in str(exc).lower() for text in _CLOSED_MESSAGES
    )


def browser_error_reason(exc: Exception) -> str | None:
    """Return safe, finite failure categories without disclosing URLs or secrets."""
    if is_browser_closed(exc):
        return "browser_session_closed"
    if isinstance(exc, (PlaywrightTimeoutError, TimeoutError)):
        return "browser_timeout"
    if isinstance(exc, PlaywrightError):
        return "browser_navigation_or_action_failed"
    return None


def browser_error_observation(tool_name: str, reason: str, *, restarted: bool) -> dict[str, Any]:
    if reason == "browser_session_closed":
        message = (
            "The browser session closed unexpectedly. "
            + ("A new session is available; previous element IDs and page state are gone. "
               if restarted else "The browser could not be restarted. ")
            + "Use browser_navigate to open a new page, or use web_search/web_extract "
            "for research. Do not claim the failed action succeeded. "
            "Do not automatically replay clicks, submissions, or other side effects."
        )
    elif reason == "browser_timeout":
        message = (
            "The browser operation timed out. Try a different URL or "
            "use web_search/web_extract. Continue with available evidence."
        )
    else:
        message = (
            "Browser automation could not complete this operation. "
            "Try another source or use web_search/web_extract, and continue the task."
        )
    return {
        "ok": False,
        "recoverable": True,
        "tool": tool_name,
        "reason": reason,
        "session_restarted": restarted,
        "message": message,
    }


async def call_browser_tool(
    manager: Any,
    name: str,
    action: Callable[[], Awaitable[Any]],
    *,
    retry_navigation_on_closed: bool = False,
) -> Any:
    """Convert transient Playwright exceptions to normal tool observations.

    When the browser died, clear stale handles and restart only for navigation.
    A second failure never causes an unbounded retry loop. Non-Playwright
    exceptions (including policy, validation and programming errors) propagate.
    """
    try:
        return await action()
    except Exception as exc:
        reason = browser_error_reason(exc)
        if reason is None:
            raise
        restarted = False
        if reason == "browser_session_closed":
            try:
                await manager.reset()
                restarted = True
            except Exception:
                logger.warning("Could not reset closed Playwright browser", exc_info=True)
            if restarted and retry_navigation_on_closed:
                try:
                    return await action()
                except Exception as retry_exc:
                    retry_reason = browser_error_reason(retry_exc)
                    if retry_reason is None:
                        raise
                    reason = retry_reason
        logger.warning("Nonfatal browser tool error: %s (%s)", name, reason)
        return browser_error_observation(name, reason, restarted=restarted)
