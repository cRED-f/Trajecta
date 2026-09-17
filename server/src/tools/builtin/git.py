"""Git helpers executed inside the Deep Agents sandbox/workspace mount."""

from __future__ import annotations

import shlex

from deepagents.backends.protocol import ExecuteResponse, SandboxBackendProtocol


class GitTools:
    def __init__(self, sandbox: SandboxBackendProtocol) -> None:
        self.sandbox = sandbox

    def run(self, args: list[str], *, timeout: int | None = None) -> ExecuteResponse:
        command = "git " + " ".join(shlex.quote(item) for item in args)
        return self.sandbox.execute(command, timeout=timeout)

    def status(self) -> ExecuteResponse:
        return self.run(["status", "--short", "--branch"])

    def diff(self, *, staged: bool = False) -> ExecuteResponse:
        return self.run(["diff", "--cached"] if staged else ["diff"])
