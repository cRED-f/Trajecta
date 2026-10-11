#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    use tauri::{Manager, RunEvent};

    tauri::Builder::default()
        // Register first so subsequent CLI invocations focus the existing window.
        .plugin(tauri_plugin_single_instance::init(|app, _, _| {
            show_main_window(app);
        }))
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_dialog::init())
        .invoke_handler(tauri::generate_handler![
            desktop_get_settings, desktop_save_settings,
            desktop_status, desktop_restart_services,
            desktop_check_updates, desktop_apply_update,
            desktop_uninstall,
        ])
        .setup(|app| {
            use tauri::menu::{Menu, MenuItem};
            use tauri::tray::TrayIconBuilder;
            let handle = app.handle().clone();
            let preferences = read_preferences(&handle);
            app.manage(DesktopProcesses::default());

            let show = MenuItem::with_id(app, "show", "Open Trajecta", true, None::<&str>)?;
            let restart = MenuItem::with_id(app, "restart", "Restart backend", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "Quit Trajecta", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&show, &restart, &quit])?;
            let mut tray = TrayIconBuilder::with_id("trajecta").menu(&menu).show_menu_on_left_click(false);
            if let Some(icon) = app.default_window_icon() {
                tray = tray.icon(icon.clone());
            }
            tray.on_menu_event(|app, event| match event.id().as_ref() {
                "show" => show_main_window(app),
                "restart" => { let _ = restart_services(app); }
                "quit" => app.exit(0),
                _ => {}
            })
            .on_tray_icon_event(|tray, event| {
                if let tauri::tray::TrayIconEvent::DoubleClick { .. } = event {
                    show_main_window(tray.app_handle());
                }
            }).build(app)?;

            // Dev mode uses a separately started dev backend and never starts production services.
            if !cfg!(debug_assertions) {
                if let Err(message) = start_services(&handle) {
                    eprintln!("Trajecta service startup: {message}");
                }
                // Keep supervising even when the window is hidden in the tray.
                // Never block model streaming or the Tauri event loop.
                let monitor = handle.clone();
                std::thread::spawn(move || {
                    while !monitor.state::<DesktopProcesses>().stopping.load(Ordering::Relaxed) {
                        std::thread::sleep(Duration::from_secs(4));
                        if monitor.state::<DesktopProcesses>().stopping.load(Ordering::Relaxed) { break; }
                        let _ = backend_health(&monitor);
                        if let Ok(base) = app_base(&monitor) {
                            ensure_bifrost(&base, &monitor.state::<DesktopProcesses>());
                        }
                    }
                });
            }
            if preferences.start_minimized || std::env::args().any(|a| a == "--tray") {
                if let Some(window) = app.get_webview_window("main") { let _ = window.hide(); }
            }
            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                if read_preferences(window.app_handle()).close_to_tray {
                    api.prevent_close();
                    let _ = window.hide();
                }
            }
        })
        .build(tauri::generate_context!())
        .expect("error building Trajecta")
        .run(|app, event| {
            if let RunEvent::Exit = event {
                app.state::<DesktopProcesses>().stopping.store(true, Ordering::Relaxed);
                stop_services(app);
            }
        });
}

use serde::{Deserialize, Serialize};
use std::{
    fs,
    io::{Read, Seek, SeekFrom, Write},
    net::{SocketAddr, TcpStream},
    path::PathBuf,
    process::{Child, Command, Stdio},
    sync::{atomic::{AtomicBool, Ordering}, Mutex},
    time::Duration,
};
use tauri::Manager;

#[derive(Default)]
struct DesktopProcesses {
    backend: Mutex<Option<Child>>,
    bifrost: Mutex<Option<Child>>,
    bifrost_error: Mutex<Option<String>>,
    bifrost_restart_attempts: Mutex<u8>,
    error: Mutex<Option<String>>,
    restart_attempts: Mutex<u8>,
    stopping: AtomicBool,
    backend_started_at: Mutex<Option<std::time::Instant>>,
    // A previous Tauri process may have left its own FastAPI backend alive.
    attached_backend: AtomicBool,
}

#[derive(Clone, Serialize, Deserialize)]
#[serde(default)]
struct DesktopPreferences {
    launch_at_login: bool,
    close_to_tray: bool,
    start_minimized: bool,
}
impl Default for DesktopPreferences {
    fn default() -> Self { Self { launch_at_login: false, close_to_tray: true, start_minimized: false } }
}

fn app_base(app: &tauri::AppHandle) -> Result<PathBuf, String> {
    if cfg!(debug_assertions) {
        // Never let a source build access installed production databases.
        return Ok(std::env::var_os("LOCALAPPDATA").map(PathBuf::from)
            .unwrap_or_else(std::env::temp_dir).join("Trajecta-Dev"));
    }
    app.path().app_local_data_dir().map_err(|e| e.to_string())
}
fn read_preferences(app: &tauri::AppHandle) -> DesktopPreferences {
    app_base(app).ok().and_then(|dir| fs::read(dir.join("desktop.json")).ok())
        .and_then(|raw| serde_json::from_slice(&raw).ok()).unwrap_or_default()
}
fn show_main_window(app: &tauri::AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.show(); let _ = window.unminimize(); let _ = window.set_focus();
    }
}
fn service_online(port: u16) -> bool {
    let addr = SocketAddr::from(([127,0,0,1], port));
    TcpStream::connect_timeout(&addr, Duration::from_millis(250)).is_ok()
}
// Do not confuse an unrelated TCP listener on 8080 with a healthy Bifrost.
fn bifrost_ready() -> bool {
    let addr = SocketAddr::from(([127, 0, 0, 1], 8080));
    let Ok(mut stream) = TcpStream::connect_timeout(&addr, Duration::from_millis(350)) else { return false; };
    if stream.set_read_timeout(Some(Duration::from_millis(600))).is_err() { return false; }
    if stream.write_all(b"GET /health HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n").is_err() { return false; }
    let mut result = [0_u8; 256];
    let Ok(n) = stream.read(&mut result) else { return false; };
    let response = String::from_utf8_lossy(&result[..n]);
    response.starts_with("HTTP/1.1 200 ") || response.starts_with("HTTP/1.0 200 ")
}

fn bifrost_setup_token(base: &std::path::Path) -> Option<String> {
    let token = fs::read_to_string(base.join("data/bifrost/setup-token")).ok()?;
    let token = token.trim();
    (token.len() == 64 && token.chars().all(|c| c.is_ascii_hexdigit())).then(|| token.to_owned())
}

// The managed key is generated once at installation and kept in user data.
// A user-supplied environment override remains available for an external gateway.
fn bifrost_virtual_key(base: &std::path::Path) -> Option<String> {
    if let Ok(value) = std::env::var("BIFROST_VIRTUAL_KEY") {
        if !value.trim().is_empty() { return Some(value.trim().to_owned()); }
    }
    let raw = fs::read_to_string(base.join("data/bifrost/virtual-key")).ok()?;
    let value = raw.trim();
    (value.starts_with("sk-bf-") && value.len() >= 22 &&
        value.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_'))
        .then(|| value.to_owned())
}

// Return a valid argv for the official pinned npm gateway launcher.
fn bifrost_launch_command(base: &std::path::Path) -> Result<Option<Command>, String> {
    let install = base.join("runtime/bifrost");
    let manifest = install.join("launcher.json");
    if !manifest.exists() { return Ok(None); }
    let raw = fs::read(&manifest).map_err(|e| format!("Bifrost manifest: {e}"))?;
    let info: serde_json::Value = serde_json::from_slice(&raw).map_err(|e| format!("Bifrost manifest: {e}"))?;
    let entry = info.get("launcher").and_then(|v| v.as_str()).ok_or("Bifrost launcher is missing")?;
    let relative = std::path::Path::new(entry);
    if relative.is_absolute() || relative.components().any(|c| !matches!(c, std::path::Component::Normal(_))) {
        return Err("Unsafe Bifrost launcher path".into());
    }
    let script = install.join(relative);
    if !script.is_file() { return Err("Bifrost launcher is missing. Re-run pnpm trajecta:install".into()); }
    let node = info.get("node_executable").and_then(|v| v.as_str())
        .filter(|v| std::path::Path::new(v).is_file()).unwrap_or("node");
    let version = info.get("transport_version").and_then(|v| v.as_str()).unwrap_or("v2.2.6");
    if !version.starts_with('v') || !version[1..].chars().all(|c| c.is_ascii_digit() || c == '.') {
        return Err("Invalid Bifrost transport version".into());
    }
    let bifrost_data = base.join("data/bifrost");
    fs::create_dir_all(&bifrost_data).map_err(|e| e.to_string())?;
    let mut cmd = Command::new(node);
    cmd.arg(script).arg("--transport-version").arg(version)
        .arg("-host").arg("127.0.0.1").arg("-port").arg("8080")
        .arg("-app-dir").arg(&bifrost_data).current_dir(&bifrost_data);
    if let Some(token) = bifrost_setup_token(base) { cmd.env("BIFROST_SETUP_TOKEN", token); }
    let key = bifrost_virtual_key(base).ok_or(
        "Managed Bifrost virtual key is missing. Re-run pnpm trajecta:install"
    )?;
    cmd.env("BIFROST_VIRTUAL_KEY", key);
    Ok(Some(cmd))
}

fn ensure_bifrost(base: &std::path::Path, processes: &DesktopProcesses) {
    let mut guard = processes.bifrost.lock().unwrap();
    if let Some(child) = guard.as_mut() {
        match child.try_wait() {
            Ok(None) => return, // Initial binary download can take time; don't duplicate it.
            Ok(Some(status)) => {
                *guard = None;
                *processes.bifrost_error.lock().unwrap() = Some(format!("Bifrost exited ({status}). See logs/bifrost.log"));
            }
            Err(error) => {
                *guard = None;
                *processes.bifrost_error.lock().unwrap() = Some(format!("Bifrost status error: {error}"));
            }
        }
    }
    if bifrost_ready() {
        *processes.bifrost_error.lock().unwrap() = None;
        return; // Externally managed gateway: never kill or replace it.
    }
    if service_online(8080) {
        *processes.bifrost_error.lock().unwrap() = Some("Port 8080 is occupied by a service that is not responding as Bifrost".into());
        return;
    }
    let mut attempts = processes.bifrost_restart_attempts.lock().unwrap();
    if *attempts >= 3 { return; } // Prevent endlessly restarting a broken installation.
    let cmd = match bifrost_launch_command(base) {
        Ok(Some(command)) => command,
        Ok(None) => {
            *processes.bifrost_error.lock().unwrap() = Some("Bifrost is not installed. Re-run pnpm trajecta:install".into());
            return;
        }
        Err(error) => {
            *processes.bifrost_error.lock().unwrap() = Some(error);
            return;
        }
    };
    *attempts += 1;
    match launch_process(cmd, base.join("logs/bifrost.log")) {
        Ok(child) => {
            *guard = Some(child);
            *processes.bifrost_error.lock().unwrap() = None;
        }
        Err(error) => *processes.bifrost_error.lock().unwrap() = Some(error),
    }
}

// A successful health response is not proof that a backend belongs to this
// installed application. In particular, do not attach to the dev server or a
// different app just because it happens to listen on 8420.
fn probe_fastapi(port: u16) -> Option<serde_json::Value> {
    let addr = SocketAddr::from(([127, 0, 0, 1], port));
    let mut stream = TcpStream::connect_timeout(&addr, Duration::from_millis(350)).ok()?;
    stream.set_read_timeout(Some(Duration::from_millis(900))).ok()?;
    stream.set_write_timeout(Some(Duration::from_millis(900))).ok()?;
    stream.write_all(b"GET /api/v1/health HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n").ok()?;
    let mut result = Vec::new();
    stream.take(8192).read_to_end(&mut result).ok()?;
    let response = String::from_utf8_lossy(&result);
    if !response.starts_with("HTTP/1.1 200 ") && !response.starts_with("HTTP/1.0 200 ") {
        return None;
    }
    let (_, body) = response.split_once("\r\n\r\n")?;
    let health: serde_json::Value = serde_json::from_str(body).ok()?;
    (health.get("status")?.as_str()? == "ok" && health.get("service")?.as_str()? == "trajecta")
        .then_some(health)
}
fn fastapi_ready(port: u16) -> bool { probe_fastapi(port).is_some() }

// Persist a nonsecret installation identifier so another Tauri instance can
// recognize an orphaned *same-installation* backend after an app crash.
fn backend_instance_id(base: &std::path::Path) -> Result<String, String> {
    let path = base.join("runtime/backend.instance");
    if let Ok(value) = fs::read_to_string(&path) {
        let value = value.trim();
        if value.len() >= 16 && value.len() <= 100 && value.chars().all(|ch| ch.is_ascii_alphanumeric() || ch == '-') {
            return Ok(value.to_owned());
        }
    }
    fs::create_dir_all(path.parent().ok_or("Invalid backend identity path")?).map_err(|e| e.to_string())?;
    let nanos = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH)
        .map_err(|e| e.to_string())?.as_nanos();
    let id = format!("{nanos:x}-{:x}", std::process::id());
    fs::write(path, &id).map_err(|e| e.to_string())?;
    Ok(id)
}
fn backend_is_ours(app: &tauri::AppHandle) -> bool {
    let Some(health) = probe_fastapi(8420) else { return false; };
    let Some(instance) = health.get("instance_id").and_then(|id| id.as_str()) else { return false; };
    app_base(app).ok().and_then(|base| backend_instance_id(&base).ok())
        .is_some_and(|expected| expected == instance)
}

// Display only a short, recognizable exception line; the full diagnostic
// remains in the local log and is never sent to a remote service.
fn failure_summary(log_path: &std::path::Path) -> Option<String> {
    let mut file = fs::File::open(log_path).ok()?;
    let len = file.metadata().ok()?.len();
    file.seek(SeekFrom::Start(len.saturating_sub(16_384))).ok()?;
    let mut tail = String::new();
    file.read_to_string(&mut tail).ok()?;
    tail.lines().rev().map(str::trim).find(|line| {
        ["ModuleNotFoundError:", "ImportError:", "RuntimeError:", "PermissionError:",
         "FileNotFoundError:", "OSError:", "ValueError:", "OperationalError:"]
            .iter().any(|prefix| line.starts_with(prefix))
    }).map(|line| line.chars().take(240).collect())
}

fn launch_process(mut command: Command, log_path: PathBuf) -> Result<Child, String> {
    use std::fs::OpenOptions;
    let log = OpenOptions::new().append(true).create(true).open(log_path).map_err(|e| e.to_string())?;
    let err = log.try_clone().map_err(|e| e.to_string())?;
    command.stdout(Stdio::from(log)).stderr(Stdio::from(err)).stdin(Stdio::null());
    #[cfg(target_os="windows")]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000); // CREATE_NO_WINDOW
    }
    command.spawn().map_err(|e| e.to_string())
}

fn start_services(app: &tauri::AppHandle) -> Result<(), String> {
    let base = app_base(app)?;
    let processes = app.state::<DesktopProcesses>();
    fs::create_dir_all(base.join("data")).map_err(|e| e.to_string())?;
    fs::create_dir_all(base.join("logs")).map_err(|e| e.to_string())?;
    let backend_python = base.join("runtime/venv/Scripts/python.exe");
    if !backend_python.exists() {
        let message = "Missing managed Python backend. Run pnpm trajecta:install from the source clone.".to_string();
        *processes.error.lock().unwrap() = Some(message.clone());
        return Err(message);
    }
    ensure_bifrost(&base, &processes);
    // Check the child we already own BEFORE probing the port. Previously the
    // supervisor rejected its own healthy backend as an external conflict.
    let mut backend = processes.backend.lock().unwrap();
    if let Some(child) = backend.as_mut() {
        match child.try_wait() {
            Ok(None) => { *processes.error.lock().unwrap() = None; return Ok(()); }
            Ok(Some(_)) | Err(_) => { *backend = None; }
        }
    }
    if service_online(8420) {
        if backend_is_ours(app) {
            // A previous desktop process exited but left this installation's
            // backend running. Reuse it; never spawn a duplicate.
            processes.attached_backend.store(true, Ordering::Relaxed);
            *processes.error.lock().unwrap() = None;
            return Ok(());
        }
        let message = "Port 8420 belongs to another process (or an older unidentifiable Trajecta backend). Close that server before restarting Trajecta. The existing process was not terminated.".to_string();
        *processes.error.lock().unwrap() = Some(message.clone());
        return Err(message);
    }
    processes.attached_backend.store(false, Ordering::Relaxed);
    let resources = base.join("runtime/backend/config");
    let mut cmd = Command::new(backend_python);
    cmd.arg("-X").arg("utf8").arg("-m").arg("server.src.main")
        .current_dir(base.join("data"))
        .env("TRAJECTA_RESOURCE_DIR", &resources)
        .env("TRAJECTA_SERVER_PORT", "8420")
        .env("TRAJECTA_BACKEND_INSTANCE_ID", backend_instance_id(&base)?)
        .env("PYTHONUTF8", "1");
    if let Some(token) = bifrost_setup_token(&base) {
        cmd.env("TRAJECTA_BIFROST_SETUP_TOKEN", token);
    }
    if let Some(key) = bifrost_virtual_key(&base) {
        cmd.env("BIFROST_VIRTUAL_KEY", key);
    }
    let child = launch_process(cmd, base.join("logs/backend.log")).map_err(|message| {
        *processes.error.lock().unwrap() = Some(format!("Unable to start FastAPI: {message}"));
        message
    })?;
    *backend = Some(child);
    *processes.backend_started_at.lock().unwrap() = Some(std::time::Instant::now());
    *processes.error.lock().unwrap() = None;
    Ok(())
}
fn stop_services(app: &tauri::AppHandle) {
    let processes = app.state::<DesktopProcesses>();
    *processes.backend_started_at.lock().unwrap() = None;
    // An attached backend was not spawned by this process. Never kill it
    // without verified OS-level ownership; it remains available for reuse.
    processes.attached_backend.store(false, Ordering::Relaxed);
    if let Ok(mut guard) = processes.backend.lock() {
        if let Some(mut child) = guard.take() { let _ = child.kill(); let _ = child.wait(); }
    }
    if let Ok(mut guard) = processes.bifrost.lock() {
        if let Some(mut child) = guard.take() {
            // The npm launcher is a parent of the actual gateway process.
            // Kill only the child tree that Trajecta itself started.
            #[cfg(target_os = "windows")]
            {
                let _ = Command::new("taskkill").args(["/PID", &child.id().to_string(), "/T", "/F"])
                    .stdout(Stdio::null()).stderr(Stdio::null()).status();
            }
            let _ = child.kill();
            let _ = child.wait();
        }
    };
}
fn restart_services(app: &tauri::AppHandle) -> Result<(), String> {
    if cfg!(debug_assertions) { return Err("Development services are managed by your dev terminal".into()); }
    // Avoid implying a restart succeeded when the backend belongs to an
    // earlier desktop process and we cannot terminate it safely.
    let processes = app.state::<DesktopProcesses>();
    if processes.attached_backend.load(Ordering::Relaxed) && backend_is_ours(app) {
        return Err("FastAPI was started by a previous Trajecta instance and is still running. Restart the original backend process before using Restart services.".into());
    }
    stop_services(app);
    *processes.restart_attempts.lock().unwrap() = 0;
    *processes.bifrost_restart_attempts.lock().unwrap() = 0;
    start_services(app)
}

// Checks managed process lifetime as well as the actual HTTP readiness probe.
// A crashed service gets one bounded automatic restart (including when Trajecta
// is minimized); repeated crashes remain visible rather than looping forever.
fn backend_health(app: &tauri::AppHandle) -> (String, Option<String>) {
    let processes = app.state::<DesktopProcesses>();
    let mut exited: Option<String> = None;
    let mut managed = false;
    {
        let mut guard = processes.backend.lock().unwrap();
        if let Some(child) = guard.as_mut() {
            match child.try_wait() {
                Ok(Some(status)) => {
                    exited = Some(format!("FastAPI exited with status {status}"));
                    *guard = None;
                }
                Err(error) => {
                    exited = Some(format!("Cannot check FastAPI process: {error}"));
                    *guard = None;
                }
                Ok(None) => managed = true,
            }
        }
    }
    // Probe once per poll. A *startup* deadline must never apply to a server
    // that has already answered its first readiness check: during LLM/tool
    // execution, one HTTP probe can time out without the backend being dead.
    // The old code kept backend_started_at forever and killed healthy servers
    // after 60 seconds if any subsequent probe was slow.
    let healthy = backend_is_ours(app);
    if healthy {
        // None now means: this child has been healthy at least once.
        *processes.backend_started_at.lock().unwrap() = None;
    }
    if managed && !healthy {
        let startup_timed_out = processes.backend_started_at.lock().unwrap()
            .as_ref().is_some_and(|at| at.elapsed() > Duration::from_secs(60));
        if startup_timed_out {
            let mut guard = processes.backend.lock().unwrap();
            if let Some(mut child) = guard.take() { let _ = child.kill(); let _ = child.wait(); }
            managed = false;
            exited = Some("FastAPI did not become ready within 60 seconds".into());
        }
    }
    if let Some(reason) = exited {
        *processes.backend_started_at.lock().unwrap() = None;
        let log_path = app_base(app).ok().map(|base| base.join("logs/backend.log"));
        let summary = log_path.as_ref().and_then(|path| failure_summary(path));
        let detail = if let Some(summary) = summary { format!("{reason}: {summary}") } else { reason };
        *processes.error.lock().unwrap() = Some(detail);
        let retry = {
            let mut attempts = processes.restart_attempts.lock().unwrap();
            if *attempts < 1 { *attempts += 1; true } else { false }
        };
        if retry && !processes.stopping.load(Ordering::Relaxed) {
            if start_services(app).is_ok() { return ("starting".into(), None); }
        }
    }
    // Reconcile survivors of a previous app process before reporting Offline.
    // The identity check prevents attaching to unrelated services.
    if healthy || backend_is_ours(app) {
        if !managed { processes.attached_backend.store(true, Ordering::Relaxed); }
        *processes.backend_started_at.lock().unwrap() = None;
        *processes.error.lock().unwrap() = None;
        return ("connected".into(), None);
    }
    if managed {
        // Do not kill or restart a live backend because a single health probe
        // failed during a long request. Only a never-ready child can time out.
        let still_starting = processes.backend_started_at.lock().unwrap().is_some();
        if still_starting { return ("starting".into(), None); }
        return ("unresponsive".into(), Some(
            "FastAPI is running but did not answer its latest health check. It will not be terminated during an active chat; check the backend log if the problem persists.".into(),
        ));
    }
    let error = processes.error.lock().unwrap().clone();
    if error.is_some() { ("failed".into(), error) }
    else { ("offline".into(), None) }
}

#[derive(Serialize)]
struct DesktopStatus {
    installed: bool,
    backend_running: bool,
    backend_state: String,
    backend_log_path: Option<String>,
    bifrost_reachable: bool,
    managed_bifrost: bool,
    bifrost_installed: bool,
    bifrost_error: Option<String>,
    error: Option<String>,
}
#[tauri::command]
fn desktop_status(app: tauri::AppHandle) -> DesktopStatus {
    let state = app.state::<DesktopProcesses>();
    // Drop the mutex guards before returning; keeping them in the tail
    // expression can outlive the borrowed managed state (E0597).
    let (backend_state, backend_error) = if cfg!(debug_assertions) {
        if fastapi_ready(8421) { ("connected".into(), None) }
        else { ("offline".into(), None) }
    } else { backend_health(&app) };
    let managed_bifrost = state.bifrost.lock().unwrap().is_some();
    let error = backend_error.or_else(|| state.error.lock().unwrap().clone());
    let backend_log_path = if cfg!(debug_assertions) { None }
        else { app_base(&app).ok().map(|dir| dir.join("logs/backend.log").display().to_string()) };
    let bifrost_error = state.bifrost_error.lock().unwrap().clone();
    DesktopStatus {
        installed: !cfg!(debug_assertions),
        backend_running: backend_state == "connected",
        backend_state,
        backend_log_path,
        bifrost_reachable: bifrost_ready(),
        managed_bifrost,
        bifrost_installed: app_base(&app).ok().is_some_and(|base| base.join("runtime/bifrost/launcher.json").exists()),
        bifrost_error,
        error,
    }
}
#[tauri::command]
fn desktop_get_settings(app: tauri::AppHandle) -> DesktopPreferences { read_preferences(&app) }

#[cfg(target_os="windows")]
fn set_windows_autostart(executable: &std::path::Path, enabled: bool) -> Result<(), String> {
    let mut command = Command::new("reg");
    command.args(["add", "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run", "/v", "Trajecta", "/t", "REG_SZ"]);
    if enabled {
        command.arg("/d").arg(format!("\"{}\" --tray", executable.display())).arg("/f");
    } else {
        command = Command::new("reg");
        command.args(["delete", "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run", "/v", "Trajecta", "/f"]);
    }
    let output = command.output().map_err(|e| e.to_string())?;
    if enabled && !output.status.success() { return Err(String::from_utf8_lossy(&output.stderr).into_owned()); }
    if !enabled && !output.status.success() {
        // Missing registry value is fine; the user requested it to be disabled.
        return Ok(());
    }
    Ok(())
}

#[tauri::command]
fn desktop_save_settings(app: tauri::AppHandle, settings: DesktopPreferences) -> Result<DesktopPreferences, String> {
    let base = app_base(&app)?;
    fs::create_dir_all(&base).map_err(|e| e.to_string())?;
    #[cfg(target_os="windows")]
    if !cfg!(debug_assertions) {
        set_windows_autostart(&base.join("runtime/Trajecta.exe"), settings.launch_at_login)?;
    }
    let raw = serde_json::to_vec_pretty(&settings).map_err(|e| e.to_string())?;
    fs::write(base.join("desktop.json"), raw).map_err(|e| e.to_string())?;
    Ok(settings)
}
#[tauri::command]
fn desktop_restart_services(app: tauri::AppHandle) -> Result<(), String> { restart_services(&app) }

#[derive(Serialize)]
struct UpdateCheck { status: String, local_commit: Option<String>, remote_commit: Option<String> }
fn git_at(root: &std::path::Path, args: &[&str]) -> Result<String, String> {
    let output = Command::new("git").args(args).current_dir(root).output().map_err(|e| e.to_string())?;
    if !output.status.success() { return Err(String::from_utf8_lossy(&output.stderr).trim().to_string()); }
    Ok(String::from_utf8_lossy(&output.stdout).trim().to_string())
}
fn clone_root(app: &tauri::AppHandle) -> Result<PathBuf, String> {
    let manifest = fs::read(app_base(app)?.join("install.json")).map_err(|e| format!("Source installation not registered: {e}"))?;
    let config: serde_json::Value = serde_json::from_slice(&manifest).map_err(|e| e.to_string())?;
    let root = config.get("source").and_then(|v| v.as_str()).ok_or("Missing source path")?;
    let root = PathBuf::from(root);
    if !root.join(".git").exists() { return Err("Original Git clone is no longer available".into()); }
    Ok(root)
}
fn check_updates_inner(app: &tauri::AppHandle) -> Result<UpdateCheck, String> {
    let root = clone_root(app)?;
    if !git_at(&root, &["status", "--porcelain"])?.is_empty() {
        return Ok(UpdateCheck { status: "local_changes".into(), local_commit: None, remote_commit: None });
    }
    let branch = git_at(&root, &["branch", "--show-current"])?;
    if branch.is_empty() { return Err("Detached HEAD; switch to a tracked branch before updating".into()); }
    let local = git_at(&root, &["rev-parse", "HEAD"])?;
    let remote_ref = format!("refs/heads/{branch}");
    let remote = git_at(&root, &["ls-remote", "origin", &remote_ref])?;
    let sha = remote.split_whitespace().next().ok_or("Remote branch not found")?.to_string();
    Ok(UpdateCheck { status: if local == sha { "current" } else { "different" }.into(), local_commit: Some(local), remote_commit: Some(sha) })
}
#[tauri::command]
async fn desktop_check_updates(app: tauri::AppHandle) -> Result<UpdateCheck, String> {
    tauri::async_runtime::spawn_blocking(move || check_updates_inner(&app))
        .await.map_err(|e| e.to_string())?
}
fn start_maintenance(app: &tauri::AppHandle, script: &str) -> Result<(), String> {
    if cfg!(debug_assertions) { return Err("Use production Trajecta to manage its installation".into()); }
    let root = clone_root(app)?;
    let location = root.join("scripts").join(script);
    if !location.exists() { return Err(format!("Missing helper: {script}")); }
    let mut command = Command::new("node");
    command.arg(location).arg(std::process::id().to_string()).current_dir(root);
    #[cfg(target_os="windows")]
    { use std::os::windows::process::CommandExt; command.creation_flags(0x08000000); }
    command.stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null());
    command.spawn().map_err(|e| e.to_string())?;
    app.exit(0);
    Ok(())
}
#[tauri::command]
fn desktop_apply_update(app: tauri::AppHandle) -> Result<(), String> {
    if check_updates_inner(&app)?.status == "local_changes" { return Err("Commit or stash local changes first".into()); }
    start_maintenance(&app, "update-local.mjs")
}
#[tauri::command]
fn desktop_uninstall(app: tauri::AppHandle) -> Result<(), String> { start_maintenance(&app, "uninstall-local.mjs") }
