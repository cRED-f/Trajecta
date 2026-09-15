"""Procedural memory — store of reusable verified skills and instructions.

Skills that pass Trajecta's evaluation pipeline are promoted to:

    /skills/<name>/SKILL.md

The path is always accessed through CompositeBackend so Deep Agents routing
behaves exactly the same inside and outside the agent runtime.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import yaml

from deepagents.backends.protocol import BackendProtocol

if TYPE_CHECKING:
    from server.src.memory.provider import MemoryProvider


SKILLS_DIR = "/skills/"
SKILL_FILE_NAME = "SKILL.md"

_SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def _validate_skill_name(name: str) -> str:
    """Validate against the Agent Skills naming rules."""
    name = name.strip()

    if not name:
        raise ValueError("Skill name cannot be empty")

    if len(name) > 64:
        raise ValueError("Skill name must be <= 64 characters")

    if not _SKILL_NAME_RE.fullmatch(name):
        raise ValueError(
            "Skill name must contain lowercase letters, numbers and "
            "single hyphens only, e.g. 'python-debugger'"
        )

    return name


def _build_skill_md(
    name: str,
    content: str,
    *,
    description: str | None = None,
) -> str:
    """Create a valid Deep Agents SKILL.md."""

    name = _validate_skill_name(name)

    description = (
        description
        or f"Reusable Trajecta skill for {name.replace('-', ' ')} tasks."
    )

    metadata = {
        "name": name,
        "description": description,
    }

    frontmatter = yaml.safe_dump(
        metadata,
        sort_keys=False,
        allow_unicode=True,
    ).strip()

    return (
        "---\n"
        f"{frontmatter}\n"
        "---\n\n"
        f"{content.strip()}\n"
    )


class ProceduralMemory:
    """Verified reusable skills stored through Deep Agents' CompositeBackend."""

    def __init__(self, provider: "MemoryProvider") -> None:
        self._provider = provider

    def _backend(self) -> BackendProtocol:
        """
        Return CompositeBackend itself.

        Do NOT use:

            backend.routes["/skills/"]

        CompositeBackend must receive the full /skills/... path so it can
        strip the route prefix before forwarding to StoreBackend.
        """
        backend = self._provider.backend

        if backend is None:
            raise RuntimeError(
                "MemoryProvider not open — call await provider.open() first"
            )

        return backend

    def _skill_path(self, name: str) -> str:
        name = _validate_skill_name(name)
        return f"{SKILLS_DIR}{name}/{SKILL_FILE_NAME}"

    async def apromote(
        self,
        name: str,
        content: str,
        *,
        description: str | None = None,
    ) -> None:
        """Create or replace a valid promoted Deep Agents skill."""

        skill_md = _build_skill_md(
            name,
            content,
            description=description,
        )

        result = await self._backend().awrite(
            self._skill_path(name),
            skill_md,
        )

        if result.error:
            raise RuntimeError(
                f"Failed to promote skill {name!r}: {result.error}"
            )

    async def alist(self) -> list[str]:
        """Return names of promoted skills."""

        result = await self._backend().als(SKILLS_DIR)

        if result.error:
            raise RuntimeError(
                f"Failed to list procedural skills: {result.error}"
            )

        names: set[str] = set()

        for entry in result.entries or []:
            # Deep Agents skill discovery considers child directories.
            if not entry.get("is_dir"):
                continue

            path = entry.get("path", "").rstrip("/")

            if not path:
                continue

            name = path.split("/")[-1]

            try:
                names.add(_validate_skill_name(name))
            except ValueError:
                continue

        return sorted(names)

    async def aload(self, name: str) -> str | None:
        """Load a skill's SKILL.md."""

        result = await self._backend().aread(
            self._skill_path(name)
        )

        if result.error or result.file_data is None:
            return None

        return result.file_data.get("content")

    async def adelete(self, name: str) -> None:
        """Delete the promoted SKILL.md."""

        result = await self._backend().adelete(
            self._skill_path(name)
        )

        if result.error:
            raise RuntimeError(
                f"Failed to delete skill {name!r}: {result.error}"
            )

    async def asearch(
        self,
        query: str,
        limit: int = 10,
    ) -> list[dict[str, str]]:
        """Simple lexical search over promoted skills."""

        query = query.strip().lower()
        results: list[dict[str, str]] = []

        for name in await self.alist():
            content = await self.aload(name) or ""

            if (
                not query
                or query in name.lower()
                or query in content.lower()
            ):
                results.append(
                    {
                        "name": name,
                        "content": content,
                    }
                )

            if len(results) >= limit:
                break

        return results
