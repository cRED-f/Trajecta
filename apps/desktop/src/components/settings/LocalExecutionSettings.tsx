import { useEffect, useState } from "react";
import { AlertTriangle, CheckCircle2, RefreshCw, Terminal } from "lucide-react";
import { request } from "../../lib/api";
import { SettingsToggle } from "./SettingsToggle";

type Config = { enabled: boolean; timeout_seconds: number };
type Status = {
  backend: string;
  available: boolean;
  active: boolean;
  message: string;
  filesystem_isolation: false;
  network_isolation: false;
  cpu_memory_limits: false;
  config: Config;
};

export function LocalExecutionSettings({ backendOnline }: { backendOnline: boolean }) {
  const [status, setStatus] = useState<Status | null>(null);
  const [draft, setDraft] = useState<Config | null>(null);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState("");
  async function refresh() {
    try {
      const next = await request<Status>("/tools/execution");
      setStatus(next); setDraft(next.config); setError("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Local executor unavailable");
    }
  }
  useEffect(() => { if (backendOnline) void refresh(); }, [backendOnline]);
  async function save() {
    if (!draft) return;
    setBusy(true); setSaved(false);
    try {
      const next = await request<Status>("/tools/execution", {
        method: "PATCH", body: JSON.stringify(draft),
      });
      setStatus(next); setDraft(next.config); setSaved(true); setError("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Cannot update local executor");
    } finally { setBusy(false); }
  }
  return <section>
    <div className="settings-page-header">
      <div><h3>Local command execution</h3><p>Run PowerShell, Python, Git, npm and other installed tools without Docker.</p></div>
    </div>
    <div className="settings-info-banner"><AlertTriangle size={15}/>
      <span><strong>Not sandboxed.</strong> Commands have the same filesystem and network access as your Windows account, even outside the selected folder. Keep Terminal permission on ASK for human approval.</span>
    </div>
    {error && <div className="settings-error-card" role="alert"><AlertTriangle size={16}/>{error}</div>}
    <div className="settings-group-card">
      <div className="settings-row"><div className="settings-row__icon"><Terminal size={16}/></div>
        <div className="settings-row__body"><strong>Local shell</strong><span>{status?.message ?? "Checking local shell…"}</span></div>
        <span className={`settings-status-pill ${status?.active ? "settings-status-pill--online" : "settings-status-pill--offline"}`}>{status?.active ? "Ready" : "Unavailable"}</span>
      </div>
      {draft && <>
        <div className="settings-row"><div className="settings-row__body"><strong>Allow agent commands</strong><span>Disabling removes execute from new agent runs. Terminal ALLOW / ASK / DENY is configured under Permissions.</span></div>
          <SettingsToggle label="Enable local execution" checked={draft.enabled} disabled={busy} onChange={(enabled) => { setDraft({...draft,enabled});setSaved(false); }}/></div>
        <div className="settings-row"><label htmlFor="execution-timeout">Command timeout</label>
          <select id="execution-timeout" value={draft.timeout_seconds} disabled={busy} onChange={(event) => setDraft({...draft,timeout_seconds:Number(event.target.value)})}>
            <option value={30}>30 seconds</option><option value={60}>1 minute</option><option value={300}>5 minutes</option><option value={600}>10 minutes</option>
          </select></div>
      </>}
    </div>
    <div className="desktop-actions"><button className="desktop-action-button" disabled={!backendOnline || busy} onClick={() => void refresh()}><RefreshCw size={15}/>Refresh status</button>
      <button className="desktop-action-button" disabled={!backendOnline || !draft || busy} onClick={() => void save()}><Terminal size={15}/>Save execution settings</button></div>
    {saved && <div className="desktop-feedback" role="status"><CheckCircle2 size={15}/>Execution settings saved for future runs.</div>}
  </section>;
}
