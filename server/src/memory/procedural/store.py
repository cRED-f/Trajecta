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

    def _skill_dir(self, name: str) -> str:
        name = _validate_skill_name(name)
        return f"{SKILLS_DIR}{name}/"

    @staticmethod
    def _validate_relative_bundle_path(path: str) -> str:
        normalised = path.replace("\\", "/").strip("/")
        if not normalised or ".." in normalised.split("/"):
            raise ValueError(f"Invalid skill bundle path: {path!r}")
        return normalised

    async def apromote_bundle(
        self,
        name: str,
        files: dict[str, str],
    ) -> None:
        """Replace the complete currently active skill bundle."""

        name = _validate_skill_name(name)

        if "SKILL.md" not in files:
            raise ValueError("A promoted skill bundle must contain SKILL.md")

        # Remove resources left over from an older version.
        await self._delete_tree(self._skill_dir(name))

        for relative, content in sorted(files.items()):
            relative = self._validate_relative_bundle_path(relative)
            target = f"{self._skill_dir(name)}{relative}"

            result = await self._backend().awrite(target, content)
            if result.error:
                raise RuntimeError(
                    f"Failed to write promoted skill file {target!r}: {result.error}"
                )

    async def aload_bundle(self, name: str) -> dict[str, str]:
        name = _validate_skill_name(name)
        result: dict[str, str] = {}
        root = self._skill_dir(name)
        await self._collect_files(root, root, result)
        return result

    async def _collect_files(
        self,
        current: str,
        root: str,
        result: dict[str, str],
    ) -> None:
        listing = await self._backend().als(current)
        if listing.error:
            return

        for entry in listing.entries or []:
            path = str(entry.get("path") or "")
            if not path:
                continue

            if entry.get("is_dir"):
                await self._collect_files(path.rstrip("/") + "/", root, result)
                continue

            read = await self._backend().aread(path)
            if read.error or read.file_data is None:
                continue

            relative = path.removeprefix(root).lstrip("/")
            result[relative] = str(read.file_data.get("content") or "")

    async def _delete_tree(self, path: str) -> None:
        listing = await self._backend().als(path)
        if listing.error:
            return

        for entry in listing.entries or []:
            entry_path = str(entry.get("path") or "")
            if not entry_path:
                continue

            if entry.get("is_dir"):
                await self._delete_tree(entry_path.rstrip("/") + "/")
            else:
                deleted = await self._backend().adelete(entry_path)
                if deleted.error:
                    raise RuntimeError(
                        f"Failed to delete stale skill file {entry_path!r}: {deleted.error}"
                    )

    async def adelete(self, name: str) -> None:
        """Delete every file belonging to the active skill."""

        await self._delete_tree(self._skill_dir(name))

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