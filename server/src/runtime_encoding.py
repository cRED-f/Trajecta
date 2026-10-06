"""Unicode-safe runtime configuration for Trajecta."""

from __future__ import annotations

import os
import sys
from typing import Any


def _reconfigure_stream(stream: Any) -> None:
    """Switch an already-open text stream to UTF-8 with a safe error policy."""

    reconfigure = getattr(
        stream,
        "reconfigure",
        None,
    )
    if not callable(reconfigure):
        return
    try:
        reconfigure(
            encoding="utf-8",
            errors="backslashreplace",
        )
    except (
        AttributeError,
        OSError,
        ValueError,
    ):
        pass


def configure_utf8_runtime() -> None:
    """
    Harden the current Trajecta process and Python child processes
    against Windows legacy encodings such as cp1252/charmap.
    """

    # Inherited by Python child processes started later on.
    os.environ.setdefault(
        "PYTHONUTF8",
        "1",
    )
    os.environ.setdefault(
        "PYTHONIOENCODING",
        "utf-8",
    )

    # Environment variables do not alter streams that already exist,
    # so fix the current process's stdout/stderr too.
    _reconfigure_stream(
        sys.stdout,
    )
    _reconfigure_stream(
        sys.stderr,
    )
