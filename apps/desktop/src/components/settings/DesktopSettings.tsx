import { useEffect, useState } from "react";
import { invoke, isTauri } from "@tauri-apps/api/core";
import { AlertCircle, CheckCircle2, GitBranch, HardDrive, Power, RefreshCcw, ShieldAlert } from "lucide-react";
import { SettingsToggle } from "./SettingsToggle";

type DesktopPrefs = {
  launch_at_login: boolean;
  close_to_tray: boolean;
  start_minimized: boolean;
};
type DesktopStatus = {
  installed: boolean;
  backend_running: boolean;
  backend_state: "connected" | "starting" | "unresponsive" | "failed" | "offline";
  backend_log_path: string | null;
  bifrost_reachable: boolean;
  managed_bifrost: boolean;
  error: string | null;
};
type UpdateCheck = {
  status: "current" | "different" | "local_changes";
  local_commit: string | null;
  remote_commit: string | null;
};

const isDesktop = isTauri();
const readableError = (value: unknown) => value instanceof Error ? value.message : String(value);

export function DesktopSettings() {
  const [prefs, setPrefs] = useState<DesktopPrefs | null>(null);
  const [status, setStatus] = useState<DesktopStatus | null>(null);
  const [update, setUpdate] = useState<UpdateCheck | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function refresh() {
    if (!isDesktop) return;
    const [settings, current] = await Promise.all([
      invoke<DesktopPrefs>("desktop_get_settings"),
      invoke<DesktopStatus>("desktop_status"),
    ]);
    setPrefs(settings);
    setStatus(current);
  }
  useEffect(() => {
    if (!isDesktop) return;
    void refresh().catch((e) => setError(readableError(e)));
    // Keep service status fresh while the user has Settings open. Status also
    // detects crashed processes and reports recovery without manual refresh.
    const timer = window.setInterval(() => {
      void invoke<DesktopStatus>("desktop_status").then(setStatus).catch(() => {});
    }, 3000);
    return () => window.clearInterval(timer);
  }, []);

  async function perform(task: () => Promise<void>) {
    setBusy(true); setError(null); setMessage(null);
    try { await task(); }
    catch (e) { setError(readableError(e)); }
    finally { setBusy(false); }
  }
  async function setOption(key: keyof DesktopPrefs, value: boolean) {
    if (!prefs) return;
    await perform(async () => {
      const saved = await invoke<DesktopPrefs>("desktop_save_settings", {
        settings: { ...prefs, [key]: value },
      });
      setPrefs(saved);
      setMessage("Desktop preferences saved.");
    });
  }

  return <section>
    <div className="settings-page-header">
      <div><h3>Desktop & Application</h3><p>Launch behavior, local services and source updates.</p></div>
    </div>
    {!isDesktop && <div className="desktop-feedback">Desktop controls are available in the Tauri app, not in a browser preview.</div>}
    {error && <div className="desktop-feedback desktop-feedback--error" role="alert"><AlertCircle size={15}/>{error}</div>}
    {message && <div className="desktop-feedback" role="status"><CheckCircle2 size={15}/>{message}</div>}

    <div className="settings-group-card">
      {([
        ["launch_at_login", "Launch at system startup", "Start Trajecta automatically when you sign in."],
        ["close_to_tray", "Close to system tray", "Keep running when the main window is closed."],
        ["start_minimized", "Start minimized", "Open in the tray without showing the main window."],
      ] as const).map(([key, title, description]) => <div className="settings-row" key={key}>
        <div className="settings-row__body"><strong>{title}</strong><span>{description}</span></div>
        <SettingsToggle label={title} checked={Boolean(prefs?.[key])} disabled={!isDesktop || !prefs || busy}
          onChange={(value) => void setOption(key, value)} />
      </div>)}
    </div>

    <div className="settings-page-header desktop-section"><div><h3>Backend services</h3><p>Trajecta manages its Python backend; Bifrost uses the installed gateway if available.</p></div></div>
    <div className="settings-group-card">
      <div className="settings-row">
        <div className="settings-row__icon"><HardDrive size={16}/></div>
        <div className="settings-row__body"><strong>FastAPI</strong><span>{status?.installed ? "Managed production backend" : "Development backend"} · local port {status?.installed ? "8420" : "8421"}</span></div>
        <span className={`settings-status-pill ${status?.backend_running ? "settings-status-pill--online" : "settings-status-pill--offline"}`}>{status?.backend_state === "connected" ? "Connected" : status?.backend_state === "starting" ? "Starting…" : status?.backend_state === "unresponsive" ? "Not responding" : status?.backend_state === "failed" ? "Failed" : "Offline"}</span>
      </div>
      <div className="settings-row">
        <div className="settings-row__icon"><Power size={16}/></div>
        <div className="settings-row__body"><strong>Bifrost</strong><span>{status?.managed_bifrost ? "Managed local gateway" : "External or optional gateway"} · port 8080</span></div>
        <span className={`settings-status-pill ${status?.bifrost_reachable ? "settings-status-pill--online" : "settings-status-pill--offline"}`}>{status?.bifrost_reachable ? "Reachable" : "Not detected"}</span>
      </div>
      {status?.error && <div className="desktop-feedback desktop-feedback--error" role="alert"><ShieldAlert size={15}/>{status.error}</div>}
      {status?.installed && !status.backend_running && status.backend_log_path && (
        <div className="desktop-feedback" role="status">Backend diagnostic log: <code>{status.backend_log_path}</code></div>
      )}
      <div className="desktop-actions">
        <button type="button" className="desktop-action-button" disabled={!isDesktop || !status?.installed || busy}
          onClick={() => void perform(async () => { await invoke("desktop_restart_services"); await refresh(); setMessage("Backend restart requested. Waiting for health check…"); })}>
          <RefreshCcw size={15}/> Restart services
        </button>
        <button type="button" className="desktop-action-button" disabled={!isDesktop || busy}
          onClick={() => void perform(async () => { await refresh(); setMessage("Diagnostics refreshed. See service status above."); })}>
          <CheckCircle2 size={15}/> Run diagnostics
        </button>
      </div>
    </div>

    <div className="settings-page-header desktop-section"><div><h3>Source updates</h3><p>Update directly from your Git clone without release packages.</p></div></div>
    <div className="settings-group-card">
      <div className="settings-row">
        <div className="settings-row__icon"><GitBranch size={16}/></div>
        <div className="settings-row__body"><strong>Git installation</strong><span>{update?.status === "current" ? "Your checkout matches the remote branch" : update?.status === "different" ? "Remote revision differs; review before updating" : update?.status === "local_changes" ? "Local modifications detected — updates blocked" : "Check the remote branch for changes"}</span></div>
      </div>
      <div className="desktop-actions">
        <button type="button" className="desktop-action-button" disabled={!isDesktop || !status?.installed || busy} onClick={() => void perform(async () => {
          const result = await invoke<UpdateCheck>("desktop_check_updates"); setUpdate(result);
          setMessage(result.status === "current" ? "You are up to date." : result.status === "different" ? "The remote branch differs from your checkout." : "Commit or stash local changes to enable updates.");
        })}><RefreshCcw size={15}/> Check for updates</button>
        {update?.status === "different" && <button type="button" className="desktop-action-button" disabled={busy} onClick={() => {
          if (!window.confirm("Update Trajecta from Git and restart? Your data will be preserved. The app will close during the build.")) return;
          void perform(() => invoke<void>("desktop_apply_update"));
        }}>Update & restart</button>}
      </div>
    </div>

    <div className="settings-page-header desktop-section"><div><h3>Uninstall</h3><p>Remove the installed runtime and launcher while keeping saved conversations and memories.</p></div></div>
    <div className="settings-group-card"><div className="desktop-actions">
      <button type="button" className="desktop-action-button desktop-action-button--danger" disabled={!isDesktop || !status?.installed || busy} onClick={() => {
        if (!window.confirm("Uninstall Trajecta? Your saved data will be kept, but the app and global launcher will be removed.")) return;
        void perform(() => invoke<void>("desktop_uninstall"));
      }}>Uninstall Trajecta</button>
    </div></div>
  </section>;
}
