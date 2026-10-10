"""Prevent incompatible multipart pins in the Windows source installer."""

from __future__ import annotations

import tomllib
from pathlib import Path

from packaging.specifiers import SpecifierSet


def test_python_multipart_supports_langchain_mcp_fastmcp_minimum() -> None:
    root = Path(__file__).resolve().parents[2]
    config = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = config["tool"]["poetry"]["dependencies"]
    selected = SpecifierSet(dependencies["python-multipart"])
    fastmcp_required = SpecifierSet(">=0.0.26")

    assert "0.0.12" not in selected
    assert "0.0.26" in selected & fastmcp_required
    assert "0.0.32" in selected & fastmcp_required
    assert "1.0.0" not in selected
