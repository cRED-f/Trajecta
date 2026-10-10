"""Catch missing LangGraph SQLite backend before desktop installation."""

from __future__ import annotations

import tomllib
from pathlib import Path



ROOT = Path(__file__).resolve().parents[2]


def test_sqlite_checkpoint_plugin_is_an_explicit_runtime_dependency() -> None:
    dependencies = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["poetry"]["dependencies"]
    # ^3.1.1 allows versions in [3.1.1,4.0.0).
    assert dependencies["langgraph-checkpoint-sqlite"] == "^3.1.1"
    assert "langgraph" in dependencies
    assert "aiosqlite" in dependencies


def test_installer_checks_sqlite_imports_before_switching_venvs() -> None:
    installer = (ROOT / "scripts/install-local.mjs").read_text(encoding="utf-8")
    assert "from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver" in installer
    assert "from langgraph.store.sqlite.aio import AsyncSqliteStore" in installer
    assert installer.index("from langgraph.checkpoint.sqlite.aio") < installer.index("renameSync(stagingVenv,venv)")
