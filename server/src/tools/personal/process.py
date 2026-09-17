"""Managed long-running host processes for personal-agent workflows.

Deep Agents' built-in ``execute`` tool runs inside the configured sandbox.  This
manager is intentionally separate: it is for user-approved host processes such
as launching a desktop-local development server, converter, or watcher.  The
provider marks mutating process tools as HITL-sensitive.
"""

from __future__ import annotations

import asyncio
import os
import signal
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from server.src.tools.personal.documents import VirtualPathResolver


_MAX_BUFFER_CHARS = 200_000


@dataclass(slots=True)
class ManagedProcess:
    id: str
    command: str
    cwd: str
    process: asyncio.subprocess.Process
    started_at: str
    stdout: list[str] = field(default_factory=list)
    stderr: list[str] = field(default_factory=list)
    readers: list[asyncio.Task[None]] = field(default_factory=list)

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "command": self.command,
            "cwd": self.cwd,
            "pid": self.process.pid,
            "running": self.process.returncode is None,
            "returncode": self.process.returncode,
            "started_at": self.started_at,
        }


class ProcessManager:
    def __init__(self, workspace_root: str, uploads_root: str) -> None:
        self._paths = VirtualPathResolver(workspace_root, uploads_root)
        self._items: dict[str, ManagedProcess] = {}
        self._lock = asyncio.Lock()

    async def _capture(
        self,
        stream: asyncio.StreamReader | None,
        target: list[str],
    ) -> None:
        if stream is None:
            return
        while True:
            chunk = await stream.read(4096)
            if not chunk:
                return
            target.append(chunk.decode("utf-8", errors="replace"))
            total = sum(len(part) for part in target)
            while target and total > _MAX_BUFFER_CHARS:
                total -= len(target.pop(0))

    def _resolve_cwd(self, virtual_path: str) -> Path:
        path = self._paths.resolve(virtual_path, writable=True)
        if not path.exists() or not path.is_dir():
            raise ValueError(f"Working directory does not exist: {virtual_path}")
        # Host process execution is deliberately constrained to /workspace.
        workspace = self._paths.workspace_root
        try:
            path.relative_to(workspace)
        except ValueError as exc:
            raise ValueError("Host processes may only run inside /workspace") from exc
        return path

    async def start(
        self,
        command: str,
        *,
        cwd: str = "/workspace/",
        env: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        if not command.strip():
            raise ValueError("command cannot be empty")
        host_cwd = self._resolve_cwd(cwd)
        clean_env = dict(os.environ)
        if env:
            # Explicit values are allowed, but never return the environment to
            # the model; credentials stay process-local.
            clean_env.update({str(k): str(v) for k, v in env.items()})

        kwargs: dict[str, Any] = {}
        if os.name != "nt":
            kwargs["start_new_session"] = True
        process = await asyncio.create_subprocess_shell(
            command,
            cwd=str(host_cwd),
            env=clean_env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **kwargs,
        )
        process_id = uuid.uuid4().hex
        item = ManagedProcess(
            id=process_id,
            command=command,
            cwd=cwd,
            process=process,
            started_at=datetime.now(UTC).isoformat(),
        )
        item.readers = [
            asyncio.create_task(self._capture(process.stdout, item.stdout)),
            asyncio.create_task(self._capture(process.stderr, item.stderr)),
        ]
        async with self._lock:
            self._items[process_id] = item
        return item.snapshot()

    async def list(self) -> list[dict[str, Any]]:
        async with self._lock:
            return [item.snapshot() for item in self._items.values()]

    async def status(self, process_id: str) -> dict[str, Any]:
        item = self._require(process_id)
        return item.snapshot()

    async def output(
        self,
        process_id: str,
        *,
        stdout_chars: int = 20_000,
        stderr_chars: int = 20_000,
    ) -> dict[str, Any]:
        item = self._require(process_id)
        stdout = "".join(item.stdout)[-max(1, min(stdout_chars, 100_000)) :]
        stderr = "".join(item.stderr)[-max(1, min(stderr_chars, 100_000)) :]
        return {**item.snapshot(), "stdout": stdout, "stderr": stderr}

    async def send_input(self, process_id: str, text: str) -> dict[str, Any]:
        item = self._require(process_id)
        if item.process.returncode is not None or item.process.stdin is None:
            raise RuntimeError("Process is not accepting input")
        item.process.stdin.write(text.encode("utf-8"))
        await item.process.stdin.drain()
        return item.snapshot()

    async def stop(self, process_id: str, *, force: bool = False) -> dict[str, Any]:
        item = self._require(process_id)
        process = item.process
        if process.returncode is None:
            if os.name != "nt" and process.pid:
                sig = signal.SIGKILL if force else signal.SIGTERM
                try:
                    os.killpg(process.pid, sig)
                except ProcessLookupError:
                    pass
            elif force:
                process.kill()
            else:
                process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5.0)
            except TimeoutError:
                process.kill()
                await process.wait()
        await asyncio.gather(*item.readers, return_exceptions=True)
        return item.snapshot()

    async def remove(self, process_id: str) -> bool:
        item = self._require(process_id)
        if item.process.returncode is None:
            raise RuntimeError("Stop a process before removing it")
        async with self._lock:
            self._items.pop(process_id, None)
        return True

    def _require(self, process_id: str) -> ManagedProcess:
        item = self._items.get(process_id)
        if item is None:
            raise ValueError(f"Unknown process id: {process_id}")
        return item

    async def close(self) -> None:
        for process_id in list(self._items):
            item = self._items.get(process_id)
            if item is not None and item.process.returncode is None:
                try:
                    await self.stop(process_id, force=True)
                except Exception:
                    pass
