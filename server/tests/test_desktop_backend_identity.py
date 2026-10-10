"""The desktop runtime can identify its own backend without changing dev health."""

import importlib.util
from pathlib import Path


HEALTH_ROUTE = Path(__file__).resolve().parents[1] / "src/api/routes/health.py"
spec = importlib.util.spec_from_file_location("desktop_health_test", HEALTH_ROUTE)
assert spec and spec.loader
health_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(health_module)


def test_unmanaged_health_does_not_claim_installation(monkeypatch):
    monkeypatch.delenv("TRAJECTA_BACKEND_INSTANCE_ID", raising=False)
    assert health_module.health() == {"status": "ok", "service": "trajecta"}


def test_managed_health_reports_installation_identity(monkeypatch):
    monkeypatch.setenv("TRAJECTA_BACKEND_INSTANCE_ID", "test-instance-12345678")
    assert health_module.health() == {
        "status": "ok", "service": "trajecta", "instance_id": "test-instance-12345678"
    }


def test_manager_checks_running_child_before_port():
    # Regression: the v40 manager refused a valid child because it tested the
    # occupied port *before* consulting its own managed process handle.
    rust = (Path(__file__).resolve().parents[2] / "apps/desktop/src-tauri/src/lib.rs").read_text(encoding="utf-8")
    start = rust.split("fn start_services(", 1)[1].split("fn stop_services(", 1)[0]
    assert start.index("let mut backend = processes.backend.lock()") < start.index("if service_online(8420)")
    assert "backend_is_ours(app)" in start
    assert 'env("TRAJECTA_BACKEND_INSTANCE_ID"' in start
    assert "Port 8420 belongs to another process" in start
