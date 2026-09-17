"""Python execution through the configured Deep Agents sandbox."""

from __future__ import annotations

import shlex

from deepagents.backends.protocol import ExecuteResponse, SandboxBackendProtocol


class PythonExecutionTools:
    def __init__(self, sandbox: SandboxBackendProtocol) -> None:
        self.sandbox = sandbox

    def execute(self, code: str, *, timeout: int | None = None) -> ExecuteResponse:
        command = f"python -c {shlex.quote(code)}"
        return self.sandbox.execute(command, timeout=timeout)
