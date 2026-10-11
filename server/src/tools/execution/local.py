"""Local Deep Agents command executor.

WARNING: Commands have the same filesystem, network, and user permissions as
Trajecta's backend process. A selected workspace is only the working directory,
NOT a security boundary. Permission policy/HITL must gate model tool calls.
"""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import uuid
from pathlib import Path, PurePosixPath
from typing import Any

from deepagents.backends.protocol import ExecuteResponse, FileDownloadResponse, FileUploadResponse
from deepagents.backends.sandbox import BaseSandbox

_OUTPUT_LIMIT = 200_000
_TIMEOUT_CODE = 124


def local_status() -> dict[str, Any]:
    shell = _shell_executable()
    return {
        "backend": "local-subprocess",
        "available": bool(shell),
        "message": (
            "Local shell is ready; commands run on this computer with your user permissions."
            if shell else "No supported local shell found."
        ),
        "filesystem_isolation": False,
        "network_isolation": False,
        "cpu_memory_limits": False,
    }


def _shell_executable() -> str | None:
    if sys.platform == "win32":
        return shutil.which("powershell.exe") or shutil.which("pwsh.exe")
    return shutil.which("sh") or ("/bin/sh" if Path("/bin/sh").is_file() else None)


def _clean_env() -> dict[str, str]:
    # Whitelist basic runtime variables rather than passing gateway API keys to
    # model-generated code. This is not isolation: host commands can still read
    # user-accessible files, launch other processes, and use the network.
    allowed = {
        "PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP",
        "TMPDIR", "HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "APPDATA",
        "LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMW6432",
        "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "LANG", "LC_ALL",
        "VIRTUAL_ENV", "PYTHONUTF8", "PYTHONIOENCODING", "NODE_PATH",
    }
    return {key: value for key, value in os.environ.items() if key.upper() in allowed}


def _terminate_tree(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    if sys.platform == "win32":
        try:
            subprocess.run(["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=5, check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.TimeoutExpired):
            pass
        if process.poll() is None:
            process.kill()
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except OSError:
            if process.poll() is None:
                process.kill()


class LocalExecutionBackend(BaseSandbox):
    """Expose Deep Agents execute via the system shell; no OS sandbox."""

    enable_capture_offload = False  # BaseSandbox helpers assume POSIX shell.

    def __init__(self, *, workspace_root: str, uploads_root: str,
                 timeout_seconds: int = 300) -> None:
        shell = _shell_executable()
        if not shell:
            raise RuntimeError("Local command execution requires an installed system shell")
        self._shell = shell
        self.workspace_root = str(Path(workspace_root).resolve(strict=True))
        self.uploads_root = str(Path(uploads_root).resolve(strict=True))
        if not Path(self.workspace_root).is_dir() or not Path(self.uploads_root).is_dir():
            raise ValueError("Execution workspace and uploads must be directories")
        self._root = Path(self.workspace_root)
        self._uploads = Path(self.uploads_root)
        self._timeout = max(1, min(3600, int(timeout_seconds)))
        self._id = f"local-{uuid.uuid4().hex[:12]}"
        self._lock = threading.RLock()
        self._running: set[subprocess.Popen[bytes]] = set()
        self._closed = False

    @property
    def id(self) -> str:
        return self._id

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        if not command or not command.strip():
            return ExecuteResponse(output="Empty command", exit_code=1)
        try:
            seconds = self._timeout if timeout is None else max(1, min(self._timeout, int(timeout)))
        except (ValueError, TypeError):
            return ExecuteResponse(output="Invalid command timeout", exit_code=1)
        # Never concatenate the command into a shell argument; pass it as one
        # argument, leaving PowerShell or /bin/sh to interpret its own syntax.
        argv = ([self._shell, "-NoProfile", "-NonInteractive", "-Command", command]
                if sys.platform == "win32" else [self._shell, "-c", command])
        with tempfile.TemporaryFile(mode="w+b") as output:
            with self._lock:
                if self._closed:
                    return ExecuteResponse(output="Local executor is closed", exit_code=250)
                try:
                    proc = subprocess.Popen(
                        argv, cwd=self.workspace_root, stdin=subprocess.DEVNULL,
                        stdout=output, stderr=subprocess.STDOUT, env=_clean_env(),
                        creationflags=(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                                       | getattr(subprocess, "CREATE_NO_WINDOW", 0)) if sys.platform == "win32" else 0,
                        start_new_session=(sys.platform != "win32"),
                    )
                except OSError as exc:
                    return ExecuteResponse(output=f"Could not start local command: {exc}", exit_code=250)
                self._running.add(proc)
            try:
                try:
                    exit_code = proc.wait(timeout=seconds)
                    suffix = ""
                except subprocess.TimeoutExpired:
                    _terminate_tree(proc)
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait()
                    exit_code = _TIMEOUT_CODE
                    suffix = f"\n[Command timed out after {seconds}s; attempted to terminate process tree]"
                output.seek(0, os.SEEK_END)
                output_size = output.tell()
                output.seek(max(0, output_size - _OUTPUT_LIMIT))
                content = output.read().decode("utf-8", errors="replace")
                return ExecuteResponse(output=content + suffix, exit_code=exit_code,
                                       truncated=output_size > _OUTPUT_LIMIT)
            finally:
                with self._lock:
                    self._running.discard(proc)

    def _resolve(self, path: str, *, writable: bool) -> Path:
        # Constrain the file-transfer API only. A shell command itself has full
        # host access; these checks do not sandbox subprocess execution.
        parts = PurePosixPath(path)
        if not path.startswith("/") or ".." in parts.parts or "\\" in path:
            raise PermissionError("Transfer paths must be absolute virtual paths")
        if parts.parts[:2] == ("/", "workspace"):
            root, relative = self._root, parts.parts[2:]
        elif parts.parts[:2] == ("/", "uploads") and not writable:
            root, relative = self._uploads, parts.parts[2:]
        else:
            raise PermissionError("Only /workspace is writable; /uploads is read-only")
        result = root.joinpath(*relative).resolve()
        if not result.is_relative_to(root):
            raise PermissionError("Transfer path escapes allowed directory")
        return result

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        results = []
        for name, content in files:
            try:
                path = self._resolve(name, writable=True)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
                results.append(FileUploadResponse(path=name))
            except (OSError, ValueError, PermissionError) as exc:
                results.append(FileUploadResponse(path=name, error="permission_denied" if isinstance(exc, PermissionError) else str(exc)))
        return results

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        results = []
        for name in paths:
            try:
                path = self._resolve(name, writable=False)
                if not path.exists():
                    results.append(FileDownloadResponse(path=name, error="file_not_found"))
                elif not path.is_file():
                    results.append(FileDownloadResponse(path=name, error="is_directory"))
                else:
                    results.append(FileDownloadResponse(path=name, content=path.read_bytes()))
            except (OSError, ValueError, PermissionError) as exc:
                results.append(FileDownloadResponse(path=name, error="permission_denied" if isinstance(exc, PermissionError) else str(exc)))
        return results

    def close(self) -> None:
        with self._lock:
            self._closed = True
            processes = list(self._running)
        for proc in processes:
            _terminate_tree(proc)
