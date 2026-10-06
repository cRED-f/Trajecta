from __future__ import annotations

import io

from server.src.runtime_encoding import (
    _reconfigure_stream,
)


def test_reconfigure_utf8_prevents_windows_charmap_failure() -> None:
    buffer = io.BytesIO()
    stream = io.TextIOWrapper(
        buffer,
        encoding="cp1252",
        errors="strict",
    )
    _reconfigure_stream(
        stream,
    )
    stream.write(
        "【 ATX 3.1】"
    )
    stream.flush()
    assert (
        buffer
        .getvalue()
        .decode("utf-8")
        == "【 ATX 3.1】"
    )
