"""Local execution contract: deliberately host access, never a sandbox.

Interactive calls remain subject to the chat runtime's Terminal HITL policy.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from server.src.tools.execution import LocalExecutionBackend, local_status
from server.src.tools.execution.local import _clean_env


@pytest.fixture()
def executor(tmp_path: Path) -> LocalExecutionBackend:
    workspace = tmp_path / "project"
    uploads = tmp_path / "uploads"
    workspace.mkdir()
    uploads.mkdir()
    runner = LocalExecutionBackend(workspace_root=str(workspace), uploads_root=str(uploads), timeout_seconds=4)
    yield runner
    runner.close()


def test_status_does_not_claim_isolation() -> None:
    status = local_status()
    assert status["filesystem_isolation"] is False
    assert status["network_isolation"] is False
    assert status["cpu_memory_limits"] is False


def test_workspace_is_working_directory(executor: LocalExecutionBackend) -> None:
    cmd = "(Get-Location).Path" if sys.platform == "win32" else "pwd"
    result = executor.execute(cmd)
    assert result.exit_code == 0
    assert Path(result.output.strip()).resolve() == Path(executor.workspace_root)


def test_nonzero_exit_and_output(executor: LocalExecutionBackend) -> None:
    cmd = "Write-Output hello; exit 7" if sys.platform == "win32" else "echo hello; exit 7"
    result = executor.execute(cmd)
    assert result.exit_code == 7
    assert "hello" in result.output


def test_timeout_terminates_command(executor: LocalExecutionBackend) -> None:
    cmd = "Start-Sleep -Seconds 8" if sys.platform == "win32" else "sleep 8"
    result = executor.execute(cmd, timeout=1)
    assert result.exit_code == 124
    assert "timed out" in result.output


def test_transfer_virtual_paths_cannot_write_uploads(executor: LocalExecutionBackend) -> None:
    result = executor.upload_files([("/uploads/not-allowed", b"x"), ("/workspace/file.txt", b"ok")])
    assert result[0].error == "permission_denied"
    assert result[1].error is None
    download = executor.download_files(["/workspace/file.txt", "/uploads/not-allowed"])
    assert download[0].content == b"ok"
    assert download[1].error == "file_not_found"


def test_secret_env_not_forwarded(monkeypatch: pytest.MonkeyPatch, executor: LocalExecutionBackend) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "not-for-local-shell")
    assert "OPENROUTER_API_KEY" not in _clean_env()


def test_close_disables_future_commands(executor: LocalExecutionBackend) -> None:
    executor.close()
    response = executor.execute("Write-Output unsafe" if sys.platform == "win32" else "echo unsafe")
    assert response.exit_code == 250
