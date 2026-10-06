"""Trajecta server entry point.

Starts the FastAPI application that serves as the Tauri sidecar.
"""

from __future__ import annotations

import os
import subprocess
import sys


def _ensure_windows_utf8_mode() -> None:
    """
    Python's UTF-8 mode must be enabled before libraries are imported.
    Setting PYTHONUTF8 after Python has already started is not sufficient,
    so on Windows we relaunch this exact command once with -X utf8.
    """
    if os.name != "nt":
        return
    if sys.flags.utf8_mode:
        return
    if os.environ.get(
        "TRAJECTA_UTF8_REEXEC"
    ) == "1":
        return

    env = os.environ.copy()
    env["TRAJECTA_UTF8_REEXEC"] = "1"
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    # Re-run the command line that started this interpreter (-m, script
    # path, extra flags) with -X utf8 inserted after the program name.
    original = getattr(
        sys,
        "orig_argv",
        None,
    )
    if not original:
        original = [
            sys.executable,
            "-m",
            "server.src.main",
            *sys.argv[1:],
        ]

    argv = [
        sys.executable,
        "-X",
        "utf8",
        *original[1:],
    ]

    # Windows cannot truly replace a running process: os.execve segfaults
    # under this CPython build and os.execv mangles paths containing
    # spaces, so spawn the UTF-8 child, forward its exit code, and stop.
    try:
        exit_code = subprocess.call(
            argv,
            env=env,
        )
    except KeyboardInterrupt:
        # The child shares this console and received the same Ctrl+C.
        exit_code = 130
    raise SystemExit(exit_code)


_ensure_windows_utf8_mode()

import uvicorn  # noqa: E402  (runs only after the UTF-8 relaunch above)

from server.src.api.app import create_app  # noqa: E402
from server.src.config import Settings  # noqa: E402


def main() -> None:
    """Launch the server."""
    settings = Settings.load()
    app = create_app(settings)
    uvicorn.run(
        app,
        host=settings.server.host,
        port=settings.server.port,
    )


if __name__ == "__main__":
    main()
