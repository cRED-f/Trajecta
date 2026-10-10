"""Deep Agents native Windows execution backend (no Docker).

The launcher creates a low-privilege AppContainer and assigns it to a Windows
Job Object *before* resuming the process. No launcher => no execute tool.
Filesystem operations on /workspace and /uploads use their existing routed
FilesystemBackend implementations. This class handles command execution and
simple file transfers, and is never an unrestricted subprocess fallback.

NOTE: AppContainer may not be able to read user-installed Python/Node modules
without separately granting READ to a trusted toolchain directory. That is an
explicit setup action, never a reason to run commands outside the sandbox.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
import threading
import uuid
from pathlib import Path, PurePosixPath
from typing import Any

from deepagents.backends.protocol import ExecuteResponse, FileDownloadResponse, FileUploadResponse
from deepagents.backends.sandbox import BaseSandbox


class NativeSandboxUnavailable(RuntimeError):
    """Fail-closed: a restricted process could not be launched."""


def helper_path() -> Path:
    """Installed helper takes priority; source developers can build under native/windows/bin."""
    override = os.getenv("TRAJECTA_NATIVE_SANDBOX_EXE")
    if override:
        return Path(override).resolve()
    installed = Path(os.getenv("LOCALAPPDATA", "")) / "ai.trajecta.desktop" / "runtime" / "trajecta-native-sandbox.exe"
    if sys.platform == "win32" and installed.is_file():
        return installed
    return Path(__file__).resolve().parents[4] / "native" / "windows" / "bin" / "trajecta-native-sandbox.exe"


def native_status() -> dict[str, Any]:
    binary = helper_path()
    available = sys.platform == "win32" and binary.is_file()
    return {
        "backend": "windows-appcontainer",
        "available": available,
        "message": (
            "Native Windows AppContainer launcher is ready (runtime isolation must still be verified)."
            if available else "Native execution requires Windows and a built trajecta-native-sandbox.exe."
        ),
        "filesystem_isolation": available,
        "network_isolation": available,
        "cpu_memory_limits": available,
        "path": str(binary) if available else None,
    }


def _memory_bytes(value: str) -> int:
    match = re.fullmatch(r"\s*(\d+)\s*([kmg]?)b?\s*", value, re.I)
    if not match:
        raise ValueError("memory_limit must be like '512m' or '1g'")
    size, suffix = int(match[1]), match[2].lower()
    amount = size * (1024 ** {"": 0, "k": 1, "m": 2, "g": 3}[suffix])
    if not 16 * 1024 * 1024 <= amount <= 16 * 1024 ** 3:
        raise ValueError("memory_limit must be between 16m and 16g")
    return amount


def _check_no_reparse_points(root: Path) -> None:
    """Never recursively add AppContainer access through junctions/symlinks."""
    if root.is_symlink():
        raise NativeSandboxUnavailable(f"Workspace is a symbolic link: {root}")
    for parent, folders, files in os.walk(root, followlinks=False):
        for name in folders + files:
            item = Path(parent) / name
            st = item.lstat()
            if getattr(st, "st_file_attributes", 0) & 0x400 or item.is_symlink():
                raise NativeSandboxUnavailable(
                    f"Workspace contains a link/reparse point: {item}. "
                    "Remove it before granting AppContainer access."
                )


class NativeWindowsSandboxBackend(BaseSandbox):
    enable_capture_offload = False  # BaseSandbox's capture script assumes POSIX shell.

    def __init__(
        self, *, workspace_root: str, uploads_root: str,
        timeout_seconds: int = 300, memory_limit: str = "512m",
        cpu_limit: float = 1.0, network_enabled: bool = False,
    ) -> None:
        if sys.platform != "win32":
            raise NativeSandboxUnavailable("Native execution requires Windows 10/11")
        self._binary = helper_path()
        if not self._binary.is_file():
            raise NativeSandboxUnavailable(
                "Native Windows sandbox launcher is missing. Re-run the Windows source installer."
            )
        self.workspace_root = str(Path(workspace_root).resolve(strict=True))
        self.uploads_root = str(Path(uploads_root).resolve(strict=True))
        self._root = Path(self.workspace_root)
        self._uploads = Path(self.uploads_root)
        self._timeout = max(1, min(3600, int(timeout_seconds)))
        self._memory_bytes = _memory_bytes(memory_limit)
        self._cpu = float(cpu_limit)
        if not 0 < self._cpu <= 64:
            raise ValueError("cpu_limit must be between 0 and 64 logical cores")
        self._network = network_enabled
        self._id = f"windows-native-{uuid.uuid4().hex[:12]}"
        fingerprint = hashlib.sha256(os.path.normcase(self.workspace_root).encode("utf-8")).hexdigest()[:24]
        self._profile = f"trajecta_{fingerprint}_{'net' if network_enabled else 'offline'}"
        self._lock = threading.RLock()
        self._closed = False
        self._sid = self._prepare_sid()
        self._granted: list[Path] = []
        try:
            self._grant(self._root, "M")
            self._grant(self._uploads, "RX")
            self._tool_path = self._provision_toolchain()
            # This is the real readiness test: an existing .exe and SID do not
            # guarantee a Windows AppContainer can launch the configured shell.
            result = self.execute("Write-Output 'TRAJECTA_SANDBOX_READY'", timeout=10)
            if result.exit_code != 0 or "TRAJECTA_SANDBOX_READY" not in result.output:
                raise NativeSandboxUnavailable(
                    "Restricted PowerShell launch failed; check native sandbox logs: "
                    + result.output[:300]
                )
        except Exception:
            for granted in reversed(self._granted):
                self._revoke(granted)
            raise

    @property
    def id(self) -> str:
        return self._id

    def _run_helper(self, args: list[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                [str(self._binary), "--profile", self._profile, *args],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=timeout, check=False, stdin=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise NativeSandboxUnavailable(f"Native launcher unavailable: {exc}") from exc

    def _prepare_sid(self) -> str:
        result = self._run_helper(["--prepare"], timeout=15)
        sid = result.stdout.strip()
        if result.returncode != 0 or not re.fullmatch(r"S-1-15-2-(?:\d+-){7}\d+", sid):
            raise NativeSandboxUnavailable(
                f"AppContainer profile creation failed: {result.stderr.strip()[:350]}"
            )
        return sid

    def _icacls(self, root: Path, action: list[str]) -> None:
        _check_no_reparse_points(root)
        try:
            result = subprocess.run(
                ["icacls.exe", str(root), *action, "/T"], capture_output=True,
                text=True, encoding="utf-8", errors="replace", timeout=120,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise NativeSandboxUnavailable(f"Cannot apply Windows folder ACLs: {exc}") from exc
        if result.returncode != 0:
            raise NativeSandboxUnavailable(
                f"AppContainer workspace permission setup failed: {result.stderr.strip()[:350]}"
            )

    def _grant(self, root: Path, rights: str) -> None:
        # Exact AppContainer SID only: no ALL APPLICATION PACKAGES / Everyone grant.
        self._icacls(root, ["/grant", f"*{self._sid}:(OI)(CI){rights}"])
        self._granted.append(root)

    def _provision_toolchain(self) -> str:
        """Whitelist executable directories; expose Python libraries READ-only.

        Never grant AppContainer read on all of %USERPROFILE% or the entire
        LocalAppData tree. Only the interpreter's Python installation and
        Trajecta's own venv are added. Other executables must already be
        accessible to an AppContainer (for example Program Files installs).
        """
        directories: set[Path] = set()
        for name in ("python", "python3", "git", "node", "npm", "npx", "pnpm", "uv"):
            executable = shutil.which(name)
            if executable:
                directories.add(Path(executable).resolve().parent)
        directories.add(Path(sys.executable).resolve().parent)
        # Grant Python package roots and native dependencies, not private
        # per-user package caches, config folders, or unrelated home data.
        windir = Path(os.environ.get("SystemRoot", "C:\\Windows")).resolve()
        program_files = Path(os.environ.get("ProgramFiles", "C:\\Program Files")).resolve()
        for folder_name in (sys.prefix, sys.base_prefix):
            folder = Path(folder_name).resolve()
            # Never recursively rewrite system installation ACLs. Windows
            # already exposes appropriate Program Files libraries to apps.
            if folder == Path(folder.anchor) or folder == windir or folder.is_relative_to(windir):
                continue
            if folder == program_files or folder.is_relative_to(program_files):
                continue
            if folder.is_dir() and folder not in self._granted:
                self._grant(folder, "RX")
        return os.pathsep.join(str(folder) for folder in sorted(directories))

    def _revoke(self, root: Path) -> None:
        try:
            self._icacls(root, ["/remove:g", f"*{self._sid}"])
        except NativeSandboxUnavailable:
            # Stale ACE never grants any other AppContainer, but should be
            # removed manually via icacls if cleanup reports an error.
            import logging
            logging.getLogger(__name__).exception("Could not revoke sandbox ACL for %s", root)

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        with self._lock:
            if self._closed:
                return ExecuteResponse(output="Native sandbox has been closed", exit_code=250)
            if not command.strip():
                return ExecuteResponse(output="Empty command", exit_code=1)
            seconds = self._timeout if timeout is None else min(self._timeout, max(1, int(timeout)))
            args = [
                "--execute", "--workspace", self.workspace_root,
                "--command", command, "--timeout-ms", str(seconds * 1000),
                "--memory-bytes", str(self._memory_bytes), "--cpu-cores", str(self._cpu),
                "--tool-path", self._tool_path,
            ]
            if self._network:
                args += ["--network"]
            try:
                result = self._run_helper(args, timeout=seconds + 15)
                output = result.stdout + result.stderr
                if result.returncode == 250:
                    output = "Native sandbox startup failed (not executed): " + output
                if result.returncode == 124:
                    output += "\n[Command timed out; sandbox process tree terminated]"
                return ExecuteResponse(output=output[-200000:], exit_code=result.returncode,
                                       truncated=len(output) > 200000)
            except NativeSandboxUnavailable as exc:
                return ExecuteResponse(output=f"Sandbox execution failed closed: {exc}", exit_code=250)

    def _resolve(self, path: str, *, writable: bool) -> Path:
        raw = PurePosixPath(path)
        if not path.startswith("/") or ".." in raw.parts or "\\" in path:
            raise PermissionError("sandbox paths must be absolute without traversal")
        if raw.parts[:2] == ("/", "workspace"):
            root, relative = self._root, raw.parts[2:]
        elif raw.parts[:2] == ("/", "uploads") and not writable:
            root, relative = self._uploads, raw.parts[2:]
        else:
            raise PermissionError("sandbox files are restricted to /workspace and read-only /uploads")
        result = root.joinpath(*relative).resolve()
        if not result.is_relative_to(root):
            raise PermissionError("sandbox path escapes permitted directory")
        return result

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        out = []
        for path, content in files:
            try:
                file = self._resolve(path, writable=True)
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_bytes(content)
                out.append(FileUploadResponse(path=path))
            except (PermissionError, OSError, ValueError) as exc:
                out.append(FileUploadResponse(path=path, error="permission_denied" if isinstance(exc, PermissionError) else str(exc)))
        return out

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        out = []
        for path in paths:
            try:
                file = self._resolve(path, writable=False)
                if not file.exists():
                    out.append(FileDownloadResponse(path=path, error="file_not_found"))
                elif not file.is_file():
                    out.append(FileDownloadResponse(path=path, error="is_directory"))
                else:
                    out.append(FileDownloadResponse(path=path, content=file.read_bytes()))
            except (PermissionError, OSError, ValueError) as exc:
                out.append(FileDownloadResponse(path=path, error="permission_denied" if isinstance(exc, PermissionError) else str(exc)))
        return out

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            for granted in reversed(self._granted):
                self._revoke(granted)
