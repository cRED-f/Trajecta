"""Per-conversation workspace folders.

A conversation may pin its own host directory. Every run then routes
``/workspace/`` (and the tools that mirror it) at that directory instead of
the process-wide ``settings.tools.workspace_root``.

Nothing here trusts a stored or caller-supplied path: it is re-validated on
every read, so a folder deleted or made unreadable after selection fails the
run with a message rather than silently falling back elsewhere.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from server.src.config import Settings
from server.src.chat.models import Conversation

METADATA_KEY = "workspace_path"


def validate_workspace_path(value: str) -> str:
    """Resolve a host folder to an absolute path, or explain why it is unusable."""

    if not value or not value.strip() or "\x00" in value:
        raise ValueError("A workspace folder is required.")

    try:
        path = Path(value).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValueError(
            "The selected workspace does not exist or is inaccessible."
        ) from exc

    if not path.is_dir():
        raise ValueError("Workspace must be a directory.")

    # Avoid exposing an entire filesystem or drive as one workspace.
    if path == Path(path.anchor):
        raise ValueError("Select a project folder instead of a filesystem root.")

    if not os.access(path, os.R_OK | os.X_OK):
        raise ValueError("Trajecta cannot access the selected directory.")

    return str(path)


def conversation_workspace(
    conversation: Conversation | dict[str, Any],
    settings: Settings,
) -> str:
    """Absolute workspace for a run.

    Falls back to the configured default when the conversation never picked a
    folder, so existing conversations keep behaving exactly as before. A
    conversation that *did* pick a folder keeps it even if that folder is now
    unreadable — raising here tells the user which folder broke instead of
    quietly writing somewhere they did not choose.
    """

    metadata = (
        conversation.metadata
        if isinstance(conversation, Conversation)
        else conversation.get("metadata") or {}
    )
    selected = metadata.get(METADATA_KEY)

    if not selected:
        # The configured default is created on startup by MemoryProvider.open(),
        # so make it here too rather than rejecting a path the app is happy to
        # use everywhere else.
        default = Path(settings.tools.workspace_root).expanduser()
        try:
            default.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ValueError(
                "The default workspace folder is not accessible."
            ) from exc
        return validate_workspace_path(str(default))

    try:
        return validate_workspace_path(str(selected))
    except ValueError as exc:
        raise ValueError(
            f"Workspace for this conversation is no longer usable: {exc}"
        ) from exc
