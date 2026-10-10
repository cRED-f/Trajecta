"""Source-level regression contract for the Tauri supervisor's chat safety.

This test is intentionally independent of Rust/Tauri build dependencies;
Windows compilation and end-to-end streaming still require testing.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUST = ROOT / "apps/desktop/src-tauri/src/lib.rs"
UI = ROOT / "apps/desktop/src/components/settings/DesktopSettings.tsx"
API = ROOT / "apps/desktop/src/lib/api.ts"


def test_health_deadline_only_applies_before_first_success():
    src = RUST.read_text(encoding="utf-8")
    health = src[src.index("fn backend_health("):src.index("struct DesktopStatus")]
    assert "let healthy = backend_is_ours(app);" in health
    assert "if healthy {" in health
    assert "*processes.backend_started_at.lock().unwrap() = None;" in health
    assert health.index("if healthy {") < health.index("let startup_timed_out")
    assert "if managed && !healthy" in health
    assert "if managed {\n        // Do not kill or restart a live backend" in health
    assert '"unresponsive".into()' in health


def test_failed_health_probe_reports_but_never_forces_restart_of_known_ready_process():
    src = RUST.read_text(encoding="utf-8")
    health = src[src.index("fn backend_health("):src.index("struct DesktopStatus")]
    tail = health[health.index("if managed {\n        // Do not kill"):]
    assert ".kill()" not in tail
    assert "start_services(app)" not in tail
    assert "unresponsive" in UI.read_text(encoding="utf-8")


def test_network_errors_do_not_mask_abort_errors():
    src = API.read_text(encoding="utf-8")
    assert "if (!(error instanceof TypeError)) throw error" in src
    assert "async function fetchBackend" in src
    assert "Open Settings → Desktop & Application" in src
    assert src.count("await fetchBackend(") >= 4
