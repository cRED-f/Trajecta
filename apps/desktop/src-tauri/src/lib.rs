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
    error: Mutex<Option<String>>,
    restart_attempts: Mutex<u8>,
    stopping: AtomicBool,
    backend_started_at: Mutex<Option<std::time::Instant>>,
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

// A TCP listener on the port is not proof that Trajecta is healthy: it could
// belong to another app, or Python could still be starting up.
fn fastapi_ready(port: u16) -> bool {
    let addr = SocketAddr::from(([127, 0, 0, 1], port));
    let Ok(mut stream) = TcpStream::connect_timeout(&addr, Duration::from_millis(350)) else {
        return false;
    };
    let _ = stream.set_read_timeout(Some(Duration::from_millis(650)));
    let _ = stream.set_write_timeout(Some(Duration::from_millis(650)));
    if stream.write_all(b"GET /api/v1/health HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n").is_err() {
        return false;
    }
    let mut result = Vec::new();
    if stream.take(4096).read_to_end(&mut result).is_err() { return false; }
    let response = String::from_utf8_lossy(&result);
    (response.starts_with("HTTP/1.1 200 ") || response.starts_with("HTTP/1.0 200 "))
        && (response.contains("\"status\":\"ok\"") || response.contains("\"status\": \"ok\""))
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
    // Bifrost is optional: manage an installed binary, otherwise use existing gateway URL.
    let bifrost_exe = base.join("runtime/bifrost/bifrost.exe");
    if bifrost_exe.exists() && !service_online(8080) {
        let mut guard = processes.bifrost.lock().unwrap();
        if guard.is_none() {
            let mut cmd = Command::new(&bifrost_exe);
            cmd.current_dir(base.join("data"));
            if let Ok(child) = launch_process(cmd, base.join("logs/bifrost.log")) { *guard = Some(child); }
        }
    }
    if service_online(8420) {
        // Avoid taking ownership of, or terminating, another service on this port.
        let message = "Port 8420 is occupied; refusing to start a second backend. Stop the other process.".to_string();
        *processes.error.lock().unwrap() = Some(message.clone());
        return Err(message);
    }
    let mut backend = processes.backend.lock().unwrap();
    if backend.is_none() {
        let resources = base.join("runtime/backend/config");
        let mut cmd = Command::new(backend_python);
        cmd.arg("-X").arg("utf8").arg("-m").arg("server.src.main")
            .current_dir(base.join("data"))
            .env("TRAJECTA_RESOURCE_DIR", &resources)
            .env("TRAJECTA_SERVER_PORT", "8420")
            .env("PYTHONUTF8", "1");
        let child = launch_process(cmd, base.join("logs/backend.log")).map_err(|message| {
            *processes.error.lock().unwrap() = Some(format!("Unable to start FastAPI: {message}"));
            message
        })?;
        *backend = Some(child);
        *processes.backend_started_at.lock().unwrap() = Some(std::time::Instant::now());
    }
    *processes.error.lock().unwrap() = None;
    Ok(())
}
fn stop_services(app: &tauri::AppHandle) {
    let processes = app.state::<DesktopProcesses>();
    *processes.backend_started_at.lock().unwrap() = None;
    for slot in [&processes.backend, &processes.bifrost] {
        if let Ok(mut guard) = slot.lock() {
            if let Some(mut child) = guard.take() { let _ = child.kill(); let _ = child.wait(); }
        }
    }
}
fn restart_services(app: &tauri::AppHandle) -> Result<(), String> {
    if cfg!(debug_assertions) { return Err("Development services are managed by your dev terminal".into()); }
    stop_services(app);
    *app.state::<DesktopProcesses>().restart_attempts.lock().unwrap() = 0;
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
    // A child can remain alive while failing to initialize the HTTP server.
    // Avoid leaving the user stuck on "Starting" indefinitely.
    if managed && !fastapi_ready(8420) {
        let timed_out = processes.backend_started_at.lock().unwrap()
            .as_ref().is_some_and(|at| at.elapsed() > Duration::from_secs(60));
        if timed_out {
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
    if fastapi_ready(8420) && managed { return ("connected".into(), None); }
    if managed { return ("starting".into(), None); }
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
    DesktopStatus {
        installed: !cfg!(debug_assertions),
        backend_running: backend_state == "connected",
        backend_state,
        backend_log_path,
        bifrost_reachable: service_online(8080),
        managed_bifrost,
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
