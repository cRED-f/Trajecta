"""Docker-backed Deep Agents sandbox.

This implements Deep Agents' ``BaseSandbox`` contract so Trajecta can use the
built-in ``execute`` tool without running arbitrary shell commands on the host.
Filesystem operations on the sandbox are inherited from ``BaseSandbox``.
"""

from __future__ import annotations

import io
import shlex
import tarfile
import threading
import uuid
from pathlib import PurePosixPath
from typing import Any

from deepagents.backends.protocol import (
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
)
from deepagents.backends.sandbox import BaseSandbox


class DockerSandboxBackend(BaseSandbox):
    """Persistent Docker container used as a Deep Agents sandbox backend."""

    enable_capture_offload = True

    def __init__(
        self,
        *,
        image: str,
        workspace_root: str,
        uploads_root: str,
        timeout_seconds: int = 300,
        memory_limit: str = "512m",
        cpu_limit: float = 1.0,
        network_enabled: bool = False,
        auto_remove: bool = True,
    ) -> None:
        try:
            import docker
        except ImportError as exc:  # pragma: no cover - dependency is declared
            raise RuntimeError("docker Python package is required") from exc

        self._docker = docker.from_env()
        self._default_timeout = max(1, int(timeout_seconds))
        self._auto_remove = auto_remove
        self._lock = threading.RLock()
        self._id = f"docker-{uuid.uuid4().hex[:12]}"
        self._workspace_root = str(workspace_root)

        volumes: dict[str, dict[str, str]] = {
            str(workspace_root): {"bind": "/workspace", "mode": "rw"},
            str(uploads_root): {"bind": "/uploads", "mode": "ro"},
        }

        nano_cpus = max(1, int(float(cpu_limit) * 1_000_000_000))
        network_mode = None if network_enabled else "none"

        self._container = self._docker.containers.run(
            image,
            command=["sh", "-lc", "while true; do sleep 3600; done"],
            detach=True,
            tty=False,
            stdin_open=False,
            working_dir="/workspace",
            volumes=volumes,
            network_mode=network_mode,
            mem_limit=memory_limit,
            nano_cpus=nano_cpus,
            labels={"trajecta.sandbox": self._id},
        )

    @property
    def id(self) -> str:
        return self._id

    @property
    def workspace_root(self) -> str:
        """Host directory mounted read-write at ``/workspace``.

        Caches are keyed on this so a container built for one project is
        never handed to another conversation that mounts a different folder.
        """
        return self._workspace_root

    @staticmethod
    def _normalize_path(path: str) -> str:
        p = PurePosixPath("/" + path.lstrip("/"))
        if ".." in p.parts:
            raise ValueError("sandbox path cannot contain '..'")
        return str(p)

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        """Execute a shell command inside the Docker container."""
        timeout_value = self._default_timeout if timeout is None else int(timeout)
        if timeout_value < 0:
            return ExecuteResponse(output="timeout must be >= 0", exit_code=2)

        # GNU/coreutils `timeout` is present in Trajecta's sandbox image. If a
        # custom image omits it, callers can pass timeout=0 to disable wrapping.
        if timeout_value > 0:
            wrapped = (
                f"timeout --signal=TERM --kill-after=5s {timeout_value}s "
                f"sh -lc {shlex.quote(command)}"
            )
        else:
            wrapped = f"sh -lc {shlex.quote(command)}"

        with self._lock:
            try:
                result = self._container.exec_run(
                    ["sh", "-lc", wrapped],
                    workdir="/workspace",
                    demux=False,
                )
            except Exception as exc:  # Docker daemon/container failure
                return ExecuteResponse(output=f"sandbox execution failed: {exc}", exit_code=None)

        raw = result.output or b""
        if isinstance(raw, tuple):
            raw = b"".join(part or b"" for part in raw)
        output = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
        return ExecuteResponse(output=output, exit_code=int(result.exit_code), truncated=False)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        responses: list[FileUploadResponse] = []
        for requested_path, content in files:
            try:
                path = self._normalize_path(requested_path)
                relative = path.lstrip("/")
                if not relative:
                    responses.append(FileUploadResponse(path=requested_path, error="invalid_path"))
                    continue

                stream = io.BytesIO()
                with tarfile.open(fileobj=stream, mode="w") as tar:
                    info = tarfile.TarInfo(name=relative)
                    info.size = len(content)
                    info.mode = 0o644
                    tar.addfile(info, io.BytesIO(content))
                stream.seek(0)

                with self._lock:
                    ok = self._container.put_archive("/", stream.getvalue())
                if not ok:
                    responses.append(
                        FileUploadResponse(path=requested_path, error="container rejected archive")
                    )
                else:
                    responses.append(FileUploadResponse(path=requested_path))
            except PermissionError:
                responses.append(FileUploadResponse(path=requested_path, error="permission_denied"))
            except Exception as exc:
                responses.append(FileUploadResponse(path=requested_path, error=str(exc)))
        return responses

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        responses: list[FileDownloadResponse] = []
        for requested_path in paths:
            try:
                path = self._normalize_path(requested_path)
                with self._lock:
                    chunks, stat = self._container.get_archive(path)
                    payload = b"".join(chunks)

                if stat.get("mode", 0) & 0o170000 == 0o040000:
                    responses.append(FileDownloadResponse(path=requested_path, error="is_directory"))
                    continue

                with tarfile.open(fileobj=io.BytesIO(payload), mode="r:*") as tar:
                    members = [member for member in tar.getmembers() if member.isfile()]
                    if not members:
                        responses.append(
                            FileDownloadResponse(path=requested_path, error="file_not_found")
                        )
                        continue
                    handle = tar.extractfile(members[0])
                    content = handle.read() if handle is not None else b""
                responses.append(FileDownloadResponse(path=requested_path, content=content))
            except Exception as exc:
                message = str(exc).lower()
                if "not found" in message or "no such" in message:
                    error: str = "file_not_found"
                elif "permission" in message:
                    error = "permission_denied"
                else:
                    error = str(exc)
                responses.append(FileDownloadResponse(path=requested_path, error=error))
        return responses

    def close(self) -> None:
        """Stop and remove the sandbox container."""
        with self._lock:
            try:
                self._container.stop(timeout=3)
            except Exception:
                pass
            if self._auto_remove:
                try:
                    self._container.remove(force=True)
                except Exception:
                    pass

    def __enter__(self) -> "DockerSandboxBackend":
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()
