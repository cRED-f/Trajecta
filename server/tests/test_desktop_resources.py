"""Regression: desktop resource files are separate from persistent local data."""
from pathlib import Path

from server.src.config import Settings


def test_desktop_resource_dir_and_port(monkeypatch, tmp_path):
    resources = tmp_path / "runtime" / "backend" / "config"
    resources.mkdir(parents=True)
    (resources / "default.yaml").write_text(
        "server:\n  port: 9999\nmemory:\n  db_path: '.trajecta/data/trajecta.db'\n",
        encoding="utf-8",
    )
    (resources / "guardrail-rules.yaml").write_text("rules: []\n", encoding="utf-8")
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.chdir(data)
    monkeypatch.setenv("TRAJECTA_RESOURCE_DIR", str(resources))
    monkeypatch.setenv("TRAJECTA_SERVER_PORT", "8420")

    settings = Settings.load()
    assert settings.server.port == 8420
    assert settings.guardrails.rules_path == str(resources / "guardrail-rules.yaml")
    assert settings.memory.db_path == ".trajecta/data/trajecta.db"
    assert Path(settings.memory.db_path).resolve().is_relative_to(data)


def test_dev_backend_port_does_not_use_production_resources(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TRAJECTA_RESOURCE_DIR", raising=False)
    monkeypatch.setenv("TRAJECTA_SERVER_PORT", "8421")
    assert Settings.load().server.port == 8421
