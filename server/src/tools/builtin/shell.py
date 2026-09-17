"""Compatibility wrapper around Deep Agents' SandboxBackendProtocol."""

from __future__ import annotations

from deepagents.backends.protocol import ExecuteResponse, SandboxBackendProtocol


class ShellTools:
    def __init__(self, sandbox: SandboxBackendProtocol) -> None:
        self.sandbox = sandbox

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        return self.sandbox.execute(command, timeout=timeout)
