"""Compatibility workspace search; Deep Agents' agent-facing tool is `grep`."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from server.src.config import Settings
from server.src.tools.personal.documents import VirtualPathResolver


class SearchTools:
    def __init__(self, settings: Settings) -> None:
        self.paths = VirtualPathResolver(settings.tools.workspace_root, settings.chat.uploads_path)

    def find_files(self, pattern: str, root: str = "/workspace/") -> list[str]:
        base = self.paths.resolve(root)
        return [self.paths.virtualize(path) for path in list(base.rglob(pattern))[:2_000]]

    def search_text(self, query: str, root: str = "/workspace/", *, regex: bool = False, limit: int = 200) -> list[dict[str, Any]]:
        base = self.paths.resolve(root)
        matcher = re.compile(query) if regex else None
        results: list[dict[str, Any]] = []
        for path in base.rglob("*"):
            if not path.is_file() or path.stat().st_size > 5_000_000:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for line_no, line in enumerate(text.splitlines(), 1):
                matched = bool(matcher.search(line)) if matcher else query.casefold() in line.casefold()
                if matched:
                    results.append({"path": self.paths.virtualize(path), "line": line_no, "text": line[:2_000]})
                    if len(results) >= limit:
                        return results
        return results
