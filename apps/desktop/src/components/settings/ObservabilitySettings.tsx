import { useEffect, useState } from "react";
import { invoke, isTauri } from "@tauri-apps/api/core";
import { useQuery } from "@tanstack/react-query";
import { Activity, ExternalLink, RefreshCw } from "lucide-react";
import { observabilityApi } from "../../lib/observability-api";
import { SettingsToggle } from "./SettingsToggle";

type Preferences = {
  launch_at_login: boolean; close_to_tray: boolean; start_minimized: boolean;
  phoenix_enabled: boolean; phoenix_sample_rate: number; phoenix_retention_days: number;
};
type Status = {
  phoenix_installed: boolean; phoenix_reachable: boolean; phoenix_managed: boolean; phoenix_error: string | null;
};

export function ObservabilitySettings({ backendOnline }: { backendOnline: boolean }) {
  const [prefs, setPrefs] = useState<Preferences | null>(null);
  const [desktopStatus, setDesktopStatus] = useState<Status | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const status = useQuery({ queryKey: ["observability", "status"], queryFn: observabilityApi.status,
    enabled: backendOnline, refetchInterval: 10000 });
  async function refresh() {
    if (!isTauri()) return;
    const [p, s] = await Promise.all([invoke<Preferences>("desktop_get_settings"), invoke<Status>("desktop_status")]);
    setPrefs(p); setDesktopStatus(s);
  }
  useEffect(() => { void refresh().catch((cause) => setError(String(cause))); }, []);
  async function saveAndRestart() {
    if (!prefs || !isTauri()) return;
    if (!window.confirm("Save observability settings and restart Trajecta services? Active chats will be interrupted.")) return;
    setBusy(true); setError(null); setMessage(null);
    try {
      await invoke("desktop_save_settings", { settings: prefs });
      await invoke("desktop_restart_services");
      await refresh(); await status.refetch();
      setMessage("Settings saved. Trace export is configured for the restarted FastAPI process.");
    } catch (cause) { setError(String(cause)); }
    finally { setBusy(false); }
  }
  return <section className="settings-page observability-settings">
    <div className="settings-page-header"><div><h3>Observability</h3><p>Local Phoenix tracing and Trajecta performance diagnostics.</p></div></div>
    <div className="settings-group-card">
      <div className="settings-row"><div><strong>Agent tracing</strong><p>Collect safe span metadata without raw model input, output, or tool content.</p></div>
        <SettingsToggle checked={prefs?.phoenix_enabled ?? false} disabled={!prefs || !isTauri()}
          label="Enable Phoenix tracing" onChange={(on) => setPrefs((p) => p && { ...p, phoenix_enabled: on })}/></div>
      <div className="settings-row"><div><strong>Trace sampling</strong><p>Percentage of traces collected after restarting the backend.</p></div>
        <select aria-label="Trace sample rate" disabled={!prefs} value={prefs?.phoenix_sample_rate ?? 1}
          onChange={(e) => setPrefs((p) => p && { ...p, phoenix_sample_rate: Number(e.target.value) })}>
          <option value={1}>100%</option><option value={0.5}>50%</option><option value={0.1}>10%</option><option value={0.01}>1%</option>
        </select></div>
      <div className="settings-row"><div><strong>Trace retention</strong><p>Default retention policy in days for the managed Phoenix server. External Phoenix has its own policy.</p></div>
        <select aria-label="Retention days" disabled={!prefs} value={prefs?.phoenix_retention_days ?? 14}
          onChange={(e) => setPrefs((p) => p && { ...p, phoenix_retention_days: Number(e.target.value) })}>
          <option value={7}>7 days</option><option value={14}>14 days</option><option value={30}>30 days</option><option value={90}>90 days</option>
        </select></div>
      <div className="observe-settings-info"><Activity size={17}/><div><strong>Privacy</strong><p>Prompt, response, reasoning, tool payload, and embedding content capture remain disabled. Phoenix is bound to 127.0.0.1. Cloud export is not offered in this interface.</p></div></div>
      <div className="desktop-actions"><button type="button" className="desktop-action-button" disabled={busy || !prefs || !isTauri()} onClick={() => void saveAndRestart()}>Save & restart services</button>
        <button type="button" className="desktop-action-button" onClick={() => void Promise.all([refresh(), status.refetch()])}><RefreshCw size={15}/> Test connection</button></div>
    </div>
    <div className="settings-group-card"><h4>Phoenix connection</h4>
      <p>Collector: <code>http://127.0.0.1:6006/v1/traces</code></p>
      <p>{status.data?.connected ? "Phoenix endpoint reachable" : "Phoenix endpoint not reachable"} · {desktopStatus?.phoenix_managed ? "Managed by Trajecta" : "External or stopped"}</p>
      <p>{desktopStatus?.phoenix_installed ? "Phoenix executable installed" : "Phoenix server not installed in the managed runtime"}</p>
      {desktopStatus?.phoenix_error && <p className="settings-error-card" role="alert">{desktopStatus.phoenix_error}</p>}
      {!desktopStatus?.phoenix_installed && <p>To bundle Phoenix into the Windows build, reinstall from source with <code>TRAJECTA_INSTALL_PHOENIX=1</code>.</p>}
      <p>Trajecta remains usable when Phoenix is stopped. Trace retention does not change existing saved chat or learning history.</p>
      <a href="https://arize.com/docs/phoenix" target="_blank" rel="noopener noreferrer" className="desktop-action-button">Phoenix documentation <ExternalLink size={14}/></a>
    </div>
    {error && <p className="settings-error-card" role="alert">{error}</p>}
    {message && <p className="observe-muted">{message}</p>}
  </section>;
}
