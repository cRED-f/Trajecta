"""Replay fixtures for deterministic skill evaluation.

A fixture captures the INITIAL local state of a task before the agent
mutates anything.

File contents use a content-addressed object store. Two trajectories that
contain the same file therefore reuse the same stored object.

Incomplete snapshots are never automatically replayed.
"""

from __future__ import annotations

import asyncio
import fnmatch
import hashlib
import json
import os
import shutil
import uuid

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from server.src.config import Settings
from server.src.memory.storage.sqlite import SQLiteDatabase
from server.src.skills.representation.skill import (
    OutcomeAssertion,
    OutcomeAssertionType,
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


_TEXT_EXTENSIONS = {
    ".txt",
    ".md",
    ".markdown",
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".css",
    ".html",
    ".htm",
    ".xml",
    ".csv",
    ".sql",
    ".toml",
    ".ini",
    ".cfg",
    ".log",
}


_JSON_EXTENSIONS = {
    ".json",
    ".jsonl",
}


class ReplayFixtureFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    virtual_path: str
    sha256: str
    size_bytes: int = Field(ge=0)
    mode: int | None = None


class ReplayFixtureManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    trajectory_id: str
    created_at: str
    complete: bool
    file_count: int = Field(ge=0)
    total_bytes: int = Field(ge=0)
    files: list[ReplayFixtureFile] = Field(default_factory=list)
    skipped: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ReplayFixtureStore:
    """Capture and restore immutable trajectory replay fixtures."""

    def __init__(self, settings: Settings, db: SQLiteDatabase) -> None:
        self._settings = settings
        self._db = db
        self._config = settings.skills.fixtures
        self._root = Path(self._config.root).resolve()
        self._objects = self._root / "objects"
        self._objects.mkdir(parents=True, exist_ok=True)
        self._workspace_root = Path(settings.tools.workspace_root).resolve()
        self._uploads_root = Path(settings.chat.uploads_path).resolve()

    async def capture_initial_state(
        self,
        *,
        trajectory_id: str,
        attachment_paths: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ReplayFixtureManifest | None:
        """Snapshot state before the agent begins acting."""
        if not self._config.enabled:
            return None

        fixture = await asyncio.to_thread(
            self._capture_sync,
            trajectory_id,
            attachment_paths or [],
            metadata or {},
        )

        payload = fixture.model_dump(mode="json")

        await self._db.execute(
            """
            INSERT INTO trajectory_replay_fixtures(
                id, trajectory_id, created_at, complete,
                file_count, total_bytes, manifest_json, metadata
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(trajectory_id) DO UPDATE SET
                id = excluded.id,
                created_at = excluded.created_at,
                complete = excluded.complete,
                file_count = excluded.file_count,
                total_bytes = excluded.total_bytes,
                manifest_json = excluded.manifest_json,
                metadata = excluded.metadata
            """,
            (
                fixture.id,
                fixture.trajectory_id,
                fixture.created_at,
                1 if fixture.complete else 0,
                fixture.file_count,
                fixture.total_bytes,
                json.dumps(payload, ensure_ascii=False),
                json.dumps(fixture.metadata, ensure_ascii=False),
            ),
        )

        await self._attach_to_trajectory(fixture)

        return fixture

    async def get(self, fixture_id: str) -> ReplayFixtureManifest | None:
        row = await self._db.fetchone(
            "SELECT manifest_json FROM trajectory_replay_fixtures WHERE id = ?",
            (fixture_id,),
        )
        if row is None:
            return None
        try:
            raw = json.loads(str(row["manifest_json"]))
            return ReplayFixtureManifest.model_validate(raw)
        except (json.JSONDecodeError, ValueError, TypeError):
            return None

    async def get_for_trajectory(self, trajectory_id: str) -> ReplayFixtureManifest | None:
        row = await self._db.fetchone(
            "SELECT manifest_json FROM trajectory_replay_fixtures WHERE trajectory_id = ?",
            (trajectory_id,),
        )
        if row is None:
            return None
        try:
            raw = json.loads(str(row["manifest_json"]))
            return ReplayFixtureManifest.model_validate(raw)
        except (json.JSONDecodeError, ValueError, TypeError):
            return None

    async def materialize(
        self,
        fixture_id: str,
        *,
        workspace_root: str | Path,
        uploads_root: str | Path,
    ) -> ReplayFixtureManifest:
        """Restore fixture into disposable evaluation directories."""
        manifest = await self.get(fixture_id)
        if manifest is None:
            raise ValueError(f"replay fixture {fixture_id!r} not found")
        if not manifest.complete:
            raise ValueError(f"replay fixture {fixture_id!r} is incomplete")

        await asyncio.to_thread(
            self._materialize_sync,
            manifest,
            Path(workspace_root).resolve(),
            Path(uploads_root).resolve(),
        )

        return manifest

    # ------------------------------------------------------------------
    # Outcome capture
    # ------------------------------------------------------------------

    async def capture_outcome(
        self,
        trajectory_id: str,
    ) -> list[OutcomeAssertion]:
        """Compare successful final state against the initial fixture.

        This produces observable outcome assertions for future held-out
        replay.
        """
        fixture = await self.get_for_trajectory(trajectory_id)

        if fixture is None:
            await self._attach_outcome(
                trajectory_id,
                assertions=[],
                complete=False,
                metadata={"reason": "initial replay fixture missing"},
            )
            return []

        if not fixture.complete:
            await self._attach_outcome(
                trajectory_id,
                assertions=[],
                complete=False,
                metadata={"reason": "initial replay fixture incomplete"},
            )
            return []

        try:
            assertions = await asyncio.to_thread(
                self._capture_outcome_sync,
                fixture,
            )
        except Exception as exc:
            await self._attach_outcome(
                trajectory_id,
                assertions=[],
                complete=False,
                metadata={"error": f"{type(exc).__name__}: {exc}"},
            )
            return []

        await self._attach_outcome(
            trajectory_id,
            assertions=assertions,
            complete=True,
            metadata={"change_count": len(assertions)},
        )

        return assertions

    async def read_object(
        self,
        digest: str,
    ) -> bytes:
        """Read one immutable fixture/outcome object after checksum validation."""
        if (
            len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest.lower())
        ):
            raise ValueError("invalid SHA-256 digest")

        return await asyncio.to_thread(self._read_object_sync, digest.lower())

    def _read_object_sync(self, digest: str) -> bytes:
        path = self._object_path(digest)

        if not path.is_file():
            raise FileNotFoundError(f"fixture object {digest} not found")

        if self._sha256(path) != digest:
            raise RuntimeError(f"fixture object {digest} failed checksum validation")

        return path.read_bytes()

    # ------------------------------------------------------------------
    # Capture
    # ------------------------------------------------------------------

    def _capture_sync(
        self,
        trajectory_id: str,
        attachment_paths: list[str],
        metadata: dict[str, Any],
    ) -> ReplayFixtureManifest:
        fixture_id = uuid.uuid4().hex
        files: list[ReplayFixtureFile] = []
        skipped: list[dict[str, Any]] = []
        total_bytes = 0
        complete = True

        max_files = self._config.max_files
        max_file_bytes = self._config.max_file_mb * 1024 * 1024
        max_total_bytes = self._config.max_total_mb * 1024 * 1024
        seen_virtual: set[str] = set()

        def add_file(host_path: Path, virtual_path: str) -> bool:
            nonlocal total_bytes, complete

            virtual_path = self._normalise_virtual_path(virtual_path)

            if virtual_path in seen_virtual:
                return True

            if len(files) >= max_files:
                complete = False
                skipped.append({"path": virtual_path, "reason": "max_files_exceeded"})
                return False

            try:
                if host_path.is_symlink():
                    complete = False
                    skipped.append({"path": virtual_path, "reason": "symlink_not_captured"})
                    return False

                stat = host_path.stat()
            except OSError as exc:
                complete = False
                skipped.append({"path": virtual_path, "reason": f"stat_failed:{type(exc).__name__}"})
                return False

            if not host_path.is_file():
                return True

            if stat.st_size > max_file_bytes:
                complete = False
                skipped.append({"path": virtual_path, "reason": "file_too_large", "size_bytes": stat.st_size})
                return False

            if total_bytes + stat.st_size > max_total_bytes:
                complete = False
                skipped.append({
                    "path": virtual_path,
                    "reason": "max_total_bytes_exceeded",
                    "size_bytes": stat.st_size,
                })
                return False

            digest = self._sha256(host_path)
            self._store_object(host_path, digest)

            files.append(
                ReplayFixtureFile(
                    virtual_path=virtual_path,
                    sha256=digest,
                    size_bytes=stat.st_size,
                    mode=stat.st_mode & 0o777,
                )
            )

            total_bytes += stat.st_size
            seen_virtual.add(virtual_path)
            return True

        # Capture bounded workspace.
        if self._config.capture_workspace:
            if self._workspace_root.exists():
                for host_path in self._iter_workspace_files(self._workspace_root):
                    relative = host_path.relative_to(self._workspace_root).as_posix()
                    add_file(host_path, f"/workspace/{relative}")
            else:
                complete = False
                skipped.append({"path": "/workspace/", "reason": "workspace_missing"})

        # Capture only the attachments belonging to this task.
        for virtual_path in attachment_paths:
            resolved = self._resolve_attachment(virtual_path)
            if resolved is None:
                complete = False
                skipped.append({
                    "path": virtual_path,
                    "reason": "attachment_path_invalid_or_missing",
                })
                continue

            host_path, normalised = resolved
            add_file(host_path, normalised)

        return ReplayFixtureManifest(
            id=fixture_id,
            trajectory_id=trajectory_id,
            created_at=_now(),
            complete=complete,
            file_count=len(files),
            total_bytes=total_bytes,
            files=files,
            skipped=skipped,
            metadata={
                **metadata,
                "workspace_root_captured": self._config.capture_workspace,
            },
        )

    def _capture_outcome_sync(
        self,
        fixture: ReplayFixtureManifest,
    ) -> list[OutcomeAssertion]:
        """Build assertions from initial workspace vs successful final workspace."""
        initial = {
            self._normalise_virtual_path(item.virtual_path): item
            for item in fixture.files
            if item.virtual_path.startswith("/workspace/")
        }

        final: dict[str, tuple[Path, str]] = {}

        if self._workspace_root.exists():
            for host_path in self._iter_workspace_files(self._workspace_root):
                relative = host_path.relative_to(self._workspace_root).as_posix()
                virtual = f"/workspace/{relative}"
                digest = self._sha256(host_path)
                final[self._normalise_virtual_path(virtual)] = (host_path, digest)

        assertions: list[OutcomeAssertion] = []
        all_paths = sorted(set(initial) | set(final))

        for virtual_path in all_paths:
            before = initial.get(virtual_path)
            after = final.get(virtual_path)

            # Deleted file
            if before is not None and after is None:
                assertions.append(
                    OutcomeAssertion(
                        type=OutcomeAssertionType.FILE_NOT_EXISTS,
                        path=virtual_path,
                        required=True,
                        weight=1.0,
                        metadata={"change": "deleted"},
                    )
                )
                continue

            # Created file
            if before is None and after is not None:
                host_path, final_hash = after
                self._store_object(host_path, final_hash)

                assertions.append(
                    OutcomeAssertion(
                        type=OutcomeAssertionType.FILE_EXISTS,
                        path=virtual_path,
                        required=True,
                        weight=0.5,
                        metadata={"change": "created"},
                    )
                )
                assertions.append(
                    self._content_assertion(
                        virtual_path=virtual_path,
                        host_path=host_path,
                        final_hash=final_hash,
                        change="created",
                    )
                )
                continue

            assert before is not None and after is not None
            host_path, final_hash = after

            # Unchanged files are not outcome evidence.
            if before.sha256 == final_hash:
                continue

            self._store_object(host_path, final_hash)

            assertions.append(
                OutcomeAssertion(
                    type=OutcomeAssertionType.FILE_CHANGED_FROM_FIXTURE,
                    path=virtual_path,
                    required=True,
                    weight=0.5,
                    metadata={
                        "change": "modified",
                        "initial_sha256": before.sha256,
                    },
                )
            )
            assertions.append(
                self._content_assertion(
                    virtual_path=virtual_path,
                    host_path=host_path,
                    final_hash=final_hash,
                    change="modified",
                )
            )

        return assertions

    def _content_assertion(
        self,
        *,
        virtual_path: str,
        host_path: Path,
        final_hash: str,
        change: str,
    ) -> OutcomeAssertion:
        suffix = host_path.suffix.lower()

        # Structured document formats are compared semantically, so
        # encoding/metadata/formatting noise does not create false failures.
        if suffix == ".pdf":
            return OutcomeAssertion(
                type=OutcomeAssertionType.PDF_SEMANTIC_EQUALS,
                path=virtual_path,
                required=True,
                weight=1.0,
                expected_object_sha256=final_hash,
                similarity_threshold=0.95,
                metadata={"change": change},
            )

        if suffix == ".docx":
            return OutcomeAssertion(
                type=OutcomeAssertionType.DOCX_SEMANTIC_EQUALS,
                path=virtual_path,
                required=True,
                weight=1.0,
                expected_object_sha256=final_hash,
                similarity_threshold=0.95,
                metadata={"change": change},
            )

        if suffix in {".xlsx", ".xlsm"}:
            return OutcomeAssertion(
                type=OutcomeAssertionType.XLSX_SEMANTIC_EQUALS,
                path=virtual_path,
                required=True,
                weight=1.0,
                expected_object_sha256=final_hash,
                similarity_threshold=1.0,
                metadata={"change": change},
            )

        if suffix == ".csv":
            return OutcomeAssertion(
                type=OutcomeAssertionType.CSV_SEMANTIC_EQUALS,
                path=virtual_path,
                required=True,
                weight=1.0,
                expected_object_sha256=final_hash,
                similarity_threshold=1.0,
                metadata={"change": change},
            )

        if suffix == ".zip":
            return OutcomeAssertion(
                type=OutcomeAssertionType.ZIP_SEMANTIC_EQUALS,
                path=virtual_path,
                required=True,
                weight=1.0,
                expected_object_sha256=final_hash,
                metadata={"change": change},
            )

        if suffix in {
            ".png",
            ".jpg",
            ".jpeg",
            ".webp",
            ".bmp",
            ".gif",
            ".tif",
            ".tiff",
        }:
            return OutcomeAssertion(
                type=OutcomeAssertionType.IMAGE_SEMANTIC_EQUALS,
                path=virtual_path,
                required=True,
                weight=1.0,
                expected_object_sha256=final_hash,
                # Allows encoding/resampling differences while still
                # requiring same dimensions and highly similar visual
                # content.
                similarity_threshold=0.85,
                metadata={"change": change},
            )

        # JSON can be compared structurally, so whitespace/order formatting
        # does not create false failures.
        if suffix in _JSON_EXTENSIONS:
            try:
                json.loads(host_path.read_text(encoding="utf-8"))
                return OutcomeAssertion(
                    type=OutcomeAssertionType.FILE_JSON_EQUALS,
                    path=virtual_path,
                    required=True,
                    weight=1.0,
                    expected_object_sha256=final_hash,
                    metadata={"change": change},
                )
            except (UnicodeDecodeError, json.JSONDecodeError):
                pass

        if suffix in _TEXT_EXTENSIONS and host_path.stat().st_size <= 2 * 1024 * 1024:
            try:
                host_path.read_text(encoding="utf-8")
                return OutcomeAssertion(
                    type=OutcomeAssertionType.FILE_TEXT_SIMILARITY,
                    path=virtual_path,
                    required=True,
                    weight=1.0,
                    expected_object_sha256=final_hash,
                    # We replay the exact same held-out task and initial state,
                    # but natural-language text can legitimately differ.
                    similarity_threshold=0.90,
                    metadata={"change": change},
                )
            except UnicodeDecodeError:
                pass

        # Binary / unknown content must match exactly.
        return OutcomeAssertion(
            type=OutcomeAssertionType.FILE_SHA256,
            path=virtual_path,
            required=True,
            weight=1.0,
            expected_sha256=final_hash,
            metadata={"change": change},
        )

    def _iter_workspace_files(self, root: Path):
        for directory, dirnames, filenames in os.walk(root, followlinks=False):
            directory_path = Path(directory)
            relative_dir = directory_path.relative_to(root).as_posix()
            if relative_dir == ".":
                relative_dir = ""

            kept_dirs: list[str] = []
            for dirname in dirnames:
                relative = f"{relative_dir}/{dirname}".strip("/")
                if not self._excluded(relative, is_dir=True):
                    kept_dirs.append(dirname)
            dirnames[:] = kept_dirs

            for filename in filenames:
                host_path = directory_path / filename
                relative = host_path.relative_to(root).as_posix()
                if self._excluded(relative, is_dir=False):
                    continue
                yield host_path

    def _excluded(self, relative: str, *, is_dir: bool) -> bool:
        relative = relative.replace("\\", "/").strip("/")
        candidates = [relative]
        if is_dir:
            candidates.append(relative + "/")

        for pattern in self._config.exclude_globs:
            pattern = pattern.replace("\\", "/")
            if any(fnmatch.fnmatch(candidate, pattern) for candidate in candidates):
                return True

            prefix = pattern.removesuffix("/**").rstrip("/")
            if prefix and (relative == prefix or relative.endswith("/" + prefix)):
                return True

        return False

    def _resolve_attachment(self, virtual_path: str) -> tuple[Path, str] | None:
        normalised = self._normalise_virtual_path(virtual_path)

        if not normalised.startswith("/uploads/"):
            return None

        relative = normalised[len("/uploads/"):]
        candidate = (self._uploads_root / relative).resolve()

        try:
            candidate.relative_to(self._uploads_root)
        except ValueError:
            return None

        if not candidate.is_file() or candidate.is_symlink():
            return None

        return candidate, normalised

    # ------------------------------------------------------------------
    # Content-addressed object storage
    # ------------------------------------------------------------------

    def _object_path(self, digest: str) -> Path:
        return self._objects / digest[:2] / digest[2:]

    def _store_object(self, source: Path, digest: str) -> None:
        target = self._object_path(digest)
        if target.exists():
            return

        target.parent.mkdir(parents=True, exist_ok=True)

        temporary = target.with_name(target.name + "." + uuid.uuid4().hex + ".tmp")
        shutil.copyfile(source, temporary)

        # File changed while snapshotting.
        if self._sha256(temporary) != digest:
            temporary.unlink(missing_ok=True)
            raise RuntimeError(f"file changed while snapshotting: {source}")

        try:
            temporary.replace(target)
        except OSError:
            temporary.unlink(missing_ok=True)
            if not target.exists():
                raise

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    # ------------------------------------------------------------------
    # Replay restore
    # ------------------------------------------------------------------

    def _materialize_sync(
        self,
        manifest: ReplayFixtureManifest,
        workspace_root: Path,
        uploads_root: Path,
    ) -> None:
        workspace_root.mkdir(parents=True, exist_ok=True)
        uploads_root.mkdir(parents=True, exist_ok=True)

        for item in manifest.files:
            virtual_path = self._normalise_virtual_path(item.virtual_path)

            if virtual_path.startswith("/workspace/"):
                relative = virtual_path[len("/workspace/"):]
                target_root = workspace_root
            elif virtual_path.startswith("/uploads/"):
                relative = virtual_path[len("/uploads/"):]
                target_root = uploads_root
            else:
                raise ValueError(f"unsupported replay path: {virtual_path!r}")

            target = (target_root / relative).resolve()

            try:
                target.relative_to(target_root)
            except ValueError as exc:
                raise ValueError(f"fixture path escapes replay root: {virtual_path!r}") from exc

            source = self._object_path(item.sha256)

            if not source.is_file():
                raise FileNotFoundError(f"fixture object {item.sha256} is missing")

            if self._sha256(source) != item.sha256:
                raise RuntimeError(f"fixture object checksum mismatch: {item.sha256}")

            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)

            if item.mode is not None:
                try:
                    target.chmod(item.mode)
                except OSError:
                    pass

    # ------------------------------------------------------------------
    # Link fixture to trajectory
    # ------------------------------------------------------------------

    async def _attach_to_trajectory(self, fixture: ReplayFixtureManifest) -> None:
        row = await self._db.fetchone(
            "SELECT metadata FROM trajectories WHERE id = ?",
            (fixture.trajectory_id,),
        )
        if row is None:
            return

        try:
            metadata = json.loads(row.get("metadata") or "{}")
        except json.JSONDecodeError:
            metadata = {}

        if not isinstance(metadata, dict):
            metadata = {}

        metadata.update({
            "replay_fixture_id": fixture.id,
            "replay_fixture_complete": fixture.complete,
            "replay_fixture_file_count": fixture.file_count,
            "replay_fixture_total_bytes": fixture.total_bytes,
        })

        await self._db.execute(
            "UPDATE trajectories SET metadata = ? WHERE id = ?",
            (
                json.dumps(metadata, ensure_ascii=False),
                fixture.trajectory_id,
            ),
        )

    @staticmethod
    def _normalise_virtual_path(value: str) -> str:
        value = str(value).replace("\\", "/").strip()
        if not value.startswith("/"):
            value = "/" + value
        while "//" in value:
            value = value.replace("//", "/")
        return value

    # ------------------------------------------------------------------
    # Link captured outcome to trajectory
    # ------------------------------------------------------------------

    async def _attach_outcome(
        self,
        trajectory_id: str,
        *,
        assertions: list[OutcomeAssertion],
        complete: bool,
        metadata: dict[str, Any],
    ) -> None:
        row = await self._db.fetchone(
            "SELECT metadata FROM trajectories WHERE id = ?",
            (trajectory_id,),
        )
        if row is None:
            return

        try:
            trajectory_metadata = json.loads(row.get("metadata") or "{}")
        except json.JSONDecodeError:
            trajectory_metadata = {}
        if not isinstance(trajectory_metadata, dict):
            trajectory_metadata = {}

        trajectory_metadata["outcome_capture_complete"] = complete
        trajectory_metadata["outcome_assertions"] = [
            assertion.model_dump(mode="json") for assertion in assertions
        ]
        trajectory_metadata["outcome_change_count"] = len(assertions)
        trajectory_metadata["outcome_capture"] = metadata

        await self._db.execute(
            "UPDATE trajectories SET metadata = ? WHERE id = ?",
            (
                json.dumps(trajectory_metadata, ensure_ascii=False),
                trajectory_id,
            ),
        )
