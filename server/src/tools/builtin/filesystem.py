"""Compatibility filesystem service.

The agent-facing filesystem tools are Deep Agents' built-ins.  This service is
kept for non-agent callers/tests and uses the same /workspace + /uploads path
boundary as Trajecta's personal tools.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from server.src.config import Settings
from server.src.tools.personal.documents import VirtualPathResolver


class FilesystemTools:
    def __init__(self, settings: Settings) -> None:
        self.paths = VirtualPathResolver(settings.tools.workspace_root, settings.chat.uploads_path)

    def read(self, path: str, *, max_bytes: int = 2_000_000) -> str:
        target = self.paths.resolve(path)
        data = target.read_bytes()[:max_bytes]
        return data.decode("utf-8", errors="replace")

    def write(self, path: str, content: str) -> dict[str, Any]:
        target = self.paths.resolve(path, writable=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return {"path": path, "bytes": target.stat().st_size}

    def list(self, path: str = "/workspace/") -> list[dict[str, Any]]:
        target = self.paths.resolve(path)
        if not target.is_dir():
            raise ValueError(f"Not a directory: {path}")
        return [
            {"name": item.name, "is_dir": item.is_dir(), "size": item.stat().st_size if item.is_file() else None}
            for item in sorted(target.iterdir(), key=lambda value: value.name.lower())
        ]

    def move(self, source: str, destination: str) -> dict[str, str]:
        src = self.paths.resolve(source, writable=True)
        dst = self.paths.resolve(destination, writable=True)
        dst.parent.mkdir(parents=True, exist_ok=True)
        src.replace(dst)
        return {"source": source, "destination": destination}

    def delete(self, path: str) -> bool:
        target = self.paths.resolve(path, writable=True)
        if target.is_dir():
            import shutil
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()
        else:
            return False
        return True
