"""Persistent local notifications with best-effort native desktop delivery."""

from __future__ import annotations

import asyncio
import os
import platform
import shutil

from server.src.tools.personal.store import PersonalAgentStore


class NotificationService:
    def __init__(self, store: PersonalAgentStore) -> None:
        self._store = store

    async def notify(self, title: str, body: str, *, level: str = "info") -> dict:
        record = await self._store.create_notification(title=title, body=body, level=level)
        await self._native(title, body)
        return record

    async def _native(self, title: str, body: str) -> None:
        system = platform.system().lower()
        try:
            if system == "darwin" and shutil.which("osascript"):
                safe_title = title.replace('"', '\\"')
                safe_body = body.replace('"', '\\"')
                proc = await asyncio.create_subprocess_exec(
                    "osascript",
                    "-e",
                    f'display notification "{safe_body}" with title "{safe_title}"',
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await proc.wait()
            elif system == "linux" and shutil.which("notify-send"):
                proc = await asyncio.create_subprocess_exec(
                    "notify-send",
                    title,
                    body,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await proc.wait()
            # Windows desktop notification delivery is intentionally left to
            # the Tauri notification plugin; persistence still works here.
        except Exception:
            # Native notification is best-effort; persistence is canonical.
            return
