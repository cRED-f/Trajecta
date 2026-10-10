"""Unit checks for native backend safety invariants.

These cannot replace real Windows AppContainer and Job Object security tests.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from server.src.tools.sandbox import NativeSandboxUnavailable, NativeWindowsSandboxBackend
from server.src.tools.sandbox.native_windows import _check_no_reparse_points, _memory_bytes


@pytest.mark.parametrize("text,expected", [
    ("512m", 512 * 1024**2),
    ("1g", 1024**3),
    ("256MB", 256 * 1024**2),
])
def test_memory_limit_parser(text: str, expected: int) -> None:
    assert _memory_bytes(text) == expected


@pytest.mark.parametrize("value", ["12m", "20g", "5gbjunk", "x", "", "0"])
def test_memory_limit_rejects_bad_values(value: str) -> None:
    with pytest.raises(ValueError):
        _memory_bytes(value)


def test_symlink_is_rejected_before_recursive_acl_changes(tmp_path: Path) -> None:
    (tmp_path / "outside").mkdir()
    work = tmp_path / "workspace"
    work.mkdir()
    link = work / "escape"
    try:
        link.symlink_to(tmp_path / "outside", target_is_directory=True)
    except OSError:
        pytest.skip("Cannot create Windows directory junction/symlink without privileges")
    with pytest.raises(NativeSandboxUnavailable, match="link/reparse"):
        _check_no_reparse_points(work)


def _backend(workspace: Path, uploads: Path) -> NativeWindowsSandboxBackend:
    backend = object.__new__(NativeWindowsSandboxBackend)
    backend._root = workspace.resolve()
    backend._uploads = uploads.resolve()
    return backend


def test_transfer_paths_never_escape_selected_project(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    uploads = tmp_path / "uploads"
    workspace.mkdir()
    uploads.mkdir()
    backend = _backend(workspace, uploads)
    assert backend._resolve("/workspace/result.txt", writable=True) == workspace / "result.txt"
    assert backend._resolve("/uploads/context.txt", writable=False) == uploads / "context.txt"
    for name in ("/workspace/../secret", "/uploads/secret", "/tmp/escape", "C:\\secret"):
        with pytest.raises(PermissionError):
            backend._resolve(name, writable=True)


def test_transfer_never_modifies_uploads(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    uploads = tmp_path / "uploads"
    workspace.mkdir()
    uploads.mkdir()
    backend = _backend(workspace, uploads)
    result = backend.upload_files([("/uploads/new.txt", b"forbidden")])
    assert result[0].error == "permission_denied"
    assert not (uploads / "new.txt").exists()


def test_execute_failure_is_never_direct_host_fallback(tmp_path: Path) -> None:
    import threading
    backend = _backend(tmp_path, tmp_path)
    backend._closed = False
    backend._lock = threading.RLock()
    backend._timeout = 5
    backend._memory_bytes = 512 * 1024 ** 2
    backend._cpu = 1.0
    backend._tool_path = ""
    backend.workspace_root = str(tmp_path)
    backend._network = False
    def unavailable(*args: object, **kwargs: object) -> object:
        raise NativeSandboxUnavailable("restricted token launch failed")
    backend._run_helper = unavailable
    result = backend.execute("Remove-Item important.txt")
    assert result.exit_code == 250
    assert "failed closed" in result.output
