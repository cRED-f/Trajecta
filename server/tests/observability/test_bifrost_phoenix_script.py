"""Node config helper must not overwrite user Bifrost configuration."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts/configure-bifrost-phoenix.mjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_bifrost_plugin_config_is_opt_in_and_idempotent(tmp_path):
    path = tmp_path / "config.json"
    original = {"providers": {"my-provider": {"secret": "never-edit-this"}},
                "governance": {"virtual_keys": [{"value": "private"}]}}
    path.write_text(json.dumps(original))
    command = ["node", str(SCRIPT), f"--config={path}"]
    preview = subprocess.run(command, capture_output=True, text=True)
    assert preview.returncode == 0, preview.stderr
    assert json.loads(path.read_text()) == original
    applied = subprocess.run(command + ["--apply"], capture_output=True, text=True)
    assert applied.returncode == 0, applied.stderr
    config = json.loads(path.read_text())
    assert config["providers"] == original["providers"]
    assert config["governance"] == original["governance"]
    assert config["plugins"][0]["config"]["trace_type"] == "open_inference"
    assert config["plugins"][0]["config"]["metrics_enabled"] is False
    first = path.read_bytes()
    subprocess.run(command + ["--apply"], check=True, capture_output=True, text=True)
    assert path.read_bytes() == first


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_bifrost_plugin_does_not_overwrite_existing_otel(tmp_path):
    path = tmp_path / "config.json"
    original = {"plugins": [{"name": "otel", "enabled": True,
                              "config": {"collector_url": "https://my-company.example"}}]}
    path.write_text(json.dumps(original))
    result = subprocess.run(["node", str(SCRIPT), "--apply", f"--config={path}"],
                            capture_output=True, text=True)
    assert result.returncode != 0
    assert json.loads(path.read_text()) == original
