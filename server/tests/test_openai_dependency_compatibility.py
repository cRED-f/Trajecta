"""Regression guard for dependency constraints in the source-based Windows installer."""

from __future__ import annotations

import tomllib
from pathlib import Path

from packaging.specifiers import SpecifierSet


ROOT = Path(__file__).resolve().parents[2]


def test_openai_sdk_range_is_compatible_with_guardrails_and_langchain_openai() -> None:
    """Guardrails 0.11 needs OpenAI <3; langchain-openai 1.6 needs >=2.45."""
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = config["tool"]["poetry"]["dependencies"]
    selected = SpecifierSet(dependencies["openai"])
    guardrails = SpecifierSet(">=2.0.0,<3.0.0")
    langchain_openai = SpecifierSet(">=2.45.0,<4.0.0")

    assert "2.45.0" in selected & guardrails & langchain_openai
    assert "3.13.0" not in selected
