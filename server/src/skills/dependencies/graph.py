"""Declared dependencies between skills.

Edges are stored in ``skill_dependencies`` and checked twice: when the
edge is added (a cycle is refused outright) and when a skill is about to
be activated (every required dependency must already be active and its
version must satisfy the constraint).
"""

from __future__ import annotations

import json

from datetime import UTC, datetime
from typing import Any

from server.src.memory.storage.sqlite import SQLiteDatabase
from server.src.skills.repository import SkillRepository


def _now() -> str:
    return datetime.now(UTC).isoformat()


class SkillDependencyGraph:
    def __init__(self, db: SQLiteDatabase, repository: SkillRepository) -> None:
        self._db = db
        self._repository = repository

    async def add(
        self,
        *,
        skill_name: str,
        depends_on_skill: str,
        version_constraint: str = "*",
        required: bool = True,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if skill_name == depends_on_skill:
            raise ValueError("a skill cannot depend on itself")

        self._validate_constraint(version_constraint)

        if not await self._skill_exists(skill_name):
            raise ValueError(f"skill {skill_name!r} not found")

        if not await self._skill_exists(depends_on_skill):
            raise ValueError(f"dependency skill {depends_on_skill!r} not found")

        graph = await self.graph()

        adjacency: dict[str, list[str]] = {
            key: [str(item["depends_on_skill"]) for item in value]
            for key, value in graph["dependencies"].items()
        }

        adjacency.setdefault(skill_name, [])

        if depends_on_skill not in adjacency[skill_name]:
            adjacency[skill_name].append(depends_on_skill)

        if self._has_cycle(adjacency):
            raise ValueError("dependency would create a cycle")

        await self._db.execute(
            """
            INSERT INTO skill_dependencies(
                skill_name,
                depends_on_skill,
                version_constraint,
                required,
                created_at,
                metadata
            )
            VALUES(?, ?, ?, ?, ?, ?)
            ON CONFLICT(skill_name, depends_on_skill)
            DO UPDATE SET
                version_constraint = excluded.version_constraint,
                required = excluded.required,
                metadata = excluded.metadata
            """,
            (
                skill_name,
                depends_on_skill,
                version_constraint,
                int(required),
                _now(),
                json.dumps(metadata or {}, ensure_ascii=False),
            ),
        )

        return await self.get(skill_name, depends_on_skill) or {}

    async def remove(self, skill_name: str, depends_on_skill: str) -> bool:
        row = await self.get(skill_name, depends_on_skill)

        if row is None:
            return False

        await self._db.execute(
            """
            DELETE FROM skill_dependencies
            WHERE skill_name = ? AND depends_on_skill = ?
            """,
            (skill_name, depends_on_skill),
        )

        return True

    async def get(
        self, skill_name: str, depends_on_skill: str
    ) -> dict[str, Any] | None:
        row = await self._db.fetchone(
            """
            SELECT * FROM skill_dependencies
            WHERE skill_name = ? AND depends_on_skill = ?
            """,
            (skill_name, depends_on_skill),
        )

        if row is None:
            return None

        return self._decode(row)

    async def list_for(self, skill_name: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch(
            """
            SELECT * FROM skill_dependencies
            WHERE skill_name = ?
            ORDER BY depends_on_skill
            """,
            (skill_name,),
        )

        return [self._decode(row) for row in rows]

    async def graph(self) -> dict[str, Any]:
        """Every edge in the table, plus whether the whole graph has a cycle."""

        rows = await self._db.fetch(
            """
            SELECT * FROM skill_dependencies
            ORDER BY skill_name, depends_on_skill
            """
        )

        dependencies: dict[str, list[dict[str, Any]]] = {}

        for row in rows:
            value = self._decode(row)
            dependencies.setdefault(str(value["skill_name"]), []).append(value)

        adjacency = {
            name: [str(item["depends_on_skill"]) for item in items]
            for name, items in dependencies.items()
        }

        return {
            "dependencies": dependencies,
            "cycle": self._has_cycle(adjacency),
        }

    async def validate_activation(self, skill_name: str) -> list[str]:
        """Blocking problems: a required dependency that is not usable."""

        errors: list[str] = []

        for edge in await self.list_for(skill_name):
            if not bool(edge["required"]):
                continue

            dependency = str(edge["depends_on_skill"])
            active = await self._repository.get_active(dependency)

            if active is None or active.get("status") != "active":
                errors.append(f"required dependency {dependency!r} is not active")
                continue

            version = str(active.get("version") or "")
            constraint = str(edge.get("version_constraint") or "*")

            if not self._matches(version, constraint):
                errors.append(
                    f"dependency {dependency!r} version {version!r} "
                    f"does not satisfy {constraint!r}"
                )

        return errors

    async def _skill_exists(self, skill_name: str) -> bool:
        if await self._repository.get_active(skill_name) is not None:
            return True

        return bool(await self._repository.list_versions(skill_name))

    @staticmethod
    def _decode(row: dict[str, Any]) -> dict[str, Any]:
        value = dict(row)
        raw = value.get("metadata")

        try:
            value["metadata"] = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            value["metadata"] = {}

        value["required"] = bool(value.get("required"))

        return value

    @classmethod
    def _validate_constraint(cls, value: str) -> None:
        if not value or value == "*":
            return

        raw = value

        for prefix in (">=", "<=", ">", "<", "==", "^", "~"):
            if raw.startswith(prefix):
                raw = raw[len(prefix) :]
                break

        cls._parse_version(raw)

    @classmethod
    def _matches(cls, version: str, constraint: str) -> bool:
        if not constraint or constraint == "*":
            return True

        current = cls._parse_version(version)

        for prefix in (">=", "<=", ">", "<", "=="):
            if constraint.startswith(prefix):
                target = cls._parse_version(constraint[len(prefix) :])

                if prefix == ">=":
                    return current >= target
                if prefix == "<=":
                    return current <= target
                if prefix == ">":
                    return current > target
                if prefix == "<":
                    return current < target
                return current == target

        if constraint.startswith("^"):
            target = cls._parse_version(constraint[1:])
            return current[0] == target[0] and current >= target

        if constraint.startswith("~"):
            target = cls._parse_version(constraint[1:])
            return current[:2] == target[:2] and current >= target

        return current == cls._parse_version(constraint)

    @staticmethod
    def _parse_version(value: str) -> tuple[int, int, int]:
        parts = value.strip().split(".")

        if len(parts) != 3 or any(not part.isdigit() for part in parts):
            raise ValueError(
                f"invalid semantic version {value!r}; expected MAJOR.MINOR.PATCH"
            )

        return (int(parts[0]), int(parts[1]), int(parts[2]))

    @staticmethod
    def _has_cycle(adjacency: dict[str, list[str]]) -> bool:
        """Depth-first search over the whole graph, nodes included as targets."""

        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node: str) -> bool:
            if node in visiting:
                return True

            if node in visited:
                return False

            visiting.add(node)

            for neighbor in adjacency.get(node, []):
                if visit(neighbor):
                    return True

            visiting.remove(node)
            visited.add(node)
            return False

        nodes = set(adjacency)

        for values in adjacency.values():
            nodes.update(values)

        return any(visit(node) for node in nodes if node not in visited)
