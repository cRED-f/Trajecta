"""Regression checks for the document dependency used by the desktop installer."""

from __future__ import annotations

import tomllib
from pathlib import Path

from packaging.specifiers import SpecifierSet


def test_supported_python_docx_release_is_installable() -> None:
    """The published 1.2.0 release must satisfy Trajecta's dependency constraint."""
    root = Path(__file__).resolve().parents[2]
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    requirement = pyproject["tool"]["poetry"]["dependencies"]["python-docx"]

    # Poetry's caret constraint ^1.2.0 translates to >=1.2.0,<2.0.0.
    specifier = (
        SpecifierSet(">=1.2.0,<2.0.0")
        if requirement == "^1.2.0"
        else SpecifierSet(requirement)
    )
    assert "1.2.0" in specifier
    assert "2.0.0" not in specifier
