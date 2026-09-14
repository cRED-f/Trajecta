"""Procedural memory — store of reusable verified skills and instructions.

Skills that pass Trajecta's evaluation pipeline are promoted to `/skills/<name>/`
as SKILL.md files in the LangGraph store (the Deep Agents `skills=` path), so the
agent loads them on demand. Trajecta's skill registry mirrors them into SQLite.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from server.src.memory.provider import MemoryProvider

SKILLS_DIR = "/skills/"
SKILL_FILE_NAME = "SKILL.md"


class ProceduralMemory:
    """Verified skills as `/skills/<name>/SKILL.md` in the store (Deep Agents skills path).

    Read-mostly: contents come from the skill registry on promotion. All public
    methods are async (the backend exposes awrite/aread/als/adelete).
    """

    def __init__(self, provider: "MemoryProvider") -> None:
        self._provider = provider

    def _backend(self) -> Any:
        backend = self._provider.backend
        if backend is None:
            raise RuntimeError("MemoryProvider not open — call await provider.open() first")
        return backend.routes[SKILLS_DIR]

    def _skill_path(self, name: str) -> str:
        return f"{SKILLS_DIR}{name.lstrip('/')}/{SKILL_FILE_NAME}"

    # -- skills CRUD -------------------------------------------------------

    async def apromote(self, name: str, content: str) -> None:
        """Write a SKILL.md for a promoted skill (skill pipeline output)."""
        await self._backend().awrite(self._skill_path(name), content)

    async def alist(self) -> list[str]:
        """Names of promoted skills in the store."""
        ls = await self._backend().als(SKILLS_DIR)
        names: list[str] = []
        for entry in getattr(ls, "entries", []) or []:
            path = entry.get("path", "") if isinstance(entry, dict) else getattr(entry, "path", "") or ""
            parts = path.strip("/").split("/")
            # "/skills/deploy/" → ["skills","deploy"] or "deploy/SKILL.md" → ["deploy","SKILL.md"]
            if len(parts) >= 2 and parts[0] == "skills":
                name = parts[1]
            elif len(parts) >= 1:
                name = parts[0]
            else:
                continue
            if name and name not in names:
                names.append(name)
        return names

    async def aload(self, name: str) -> str | None:
        """Content of a skill's SKILL.md (None when missing)."""
        res = await self._backend().aread(self._skill_path(name))
        if res.error or res.file_data is None:
            return None
        return res.file_data.get("content")

    async def asearch(self, query: str, limit: int = 10) -> list[dict]:
        """Search skill names + contents (best-effort lexical match)."""
        matches: list[dict] = []
        for name in await self.alist():
            content = await self.aload(name) or ""
            if query.lower() in name.lower() or query.lower() in content.lower():
                matches.append({"name": name, "content": content})
        return matches[:limit]