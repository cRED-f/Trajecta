"""Opt-in host desktop control for Trajecta.

All mutating functions are marked HITL-sensitive by PersonalToolProvider.  The
module imports PyAutoGUI lazily so servers without a GUI can still start.
"""

from __future__ import annotations

import uuid
from typing import Any

from server.src.config import Settings
from server.src.tools.personal.documents import VirtualPathResolver


class ComputerManager:
    def __init__(self, settings: Settings) -> None:
        self._paths = VirtualPathResolver(settings.tools.workspace_root, settings.chat.uploads_path)

    @staticmethod
    def _gui() -> Any:
        try:
            import pyautogui
        except ImportError as exc:
            raise RuntimeError("pyautogui is required for computer-use tools") from exc
        pyautogui.FAILSAFE = True
        return pyautogui

    def screenshot(self) -> dict[str, Any]:
        gui = self._gui()
        virtual = f"/workspace/.trajecta/computer/{uuid.uuid4().hex}.png"
        path = self._paths.resolve(virtual, writable=True)
        path.parent.mkdir(parents=True, exist_ok=True)
        gui.screenshot(str(path))
        return {
            "path": virtual,
            "instruction": "Use read_file on this path to inspect the screenshot multimodally.",
        }

    def screen_size(self) -> dict[str, int]:
        size = self._gui().size()
        return {"width": int(size.width), "height": int(size.height)}

    def click(self, x: int, y: int, *, button: str = "left", clicks: int = 1) -> dict[str, Any]:
        self._gui().click(x=x, y=y, button=button, clicks=clicks)
        return {"ok": True, "x": x, "y": y, "button": button, "clicks": clicks}

    def move(self, x: int, y: int, *, duration: float = 0.2) -> dict[str, Any]:
        self._gui().moveTo(x, y, duration=max(0.0, min(duration, 5.0)))
        return {"ok": True, "x": x, "y": y}

    def drag(self, x: int, y: int, *, duration: float = 0.5, button: str = "left") -> dict[str, Any]:
        self._gui().dragTo(x, y, duration=max(0.0, min(duration, 10.0)), button=button)
        return {"ok": True, "x": x, "y": y, "button": button}

    def scroll(self, amount: int, *, x: int | None = None, y: int | None = None) -> dict[str, Any]:
        self._gui().scroll(amount, x=x, y=y)
        return {"ok": True, "amount": amount}

    def type(self, text: str, *, interval: float = 0.01) -> dict[str, Any]:
        self._gui().write(text, interval=max(0.0, min(interval, 1.0)))
        return {"ok": True, "characters": len(text)}

    def key(self, key: str) -> dict[str, Any]:
        self._gui().press(key)
        return {"ok": True, "key": key}

    def shortcut(self, keys: list[str]) -> dict[str, Any]:
        if not keys:
            raise ValueError("keys cannot be empty")
        self._gui().hotkey(*keys)
        return {"ok": True, "keys": keys}
