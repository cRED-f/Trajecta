import { useEffect, useState } from "react";
import { AlertCircle, CheckCircle2, RefreshCw, ShieldCheck, Terminal } from "lucide-react";
import { request } from "../../lib/api";
import { SettingsToggle } from "./SettingsToggle";

type Config = {
  enabled: boolean;
  timeout_seconds: number;
  memory_limit: string;
  cpu_limit: number;
  network_enabled: boolean;
};
type Status = {
  backend: string;
  available: boolean;
  active: boolean;
  message: string;
  filesystem_isolation: boolean;
  network_isolation: boolean;
  cpu_memory_limits: boolean;
  config: Config;
};

export function SandboxSettings({ backendOnline }: { backendOnline: boolean }) {
  const [status, setStatus] = useState<Status | null>(null);
  const [draft, setDraft] = useState<Config | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  async function refresh() {
    try {
      const next = await request<Status>("/tools/sandbox");
      setStatus(next);
      setDraft(next.config);
      setError("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Sandbox status unavailable");
    }
  }
  useEffect(() => { if (backendOnline) void refresh(); }, [backendOnline]);
  async function save() {
    if (!draft) return;
    setBusy(true); setSaved(false);
    try {
      const next = await request<Status>("/tools/sandbox", {
        method: "PATCH", body: JSON.stringify(draft),
      });
      setStatus(next); setDraft(next.config); setSaved(true); setError("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Cannot update sandbox");
    } finally { setBusy(false); }
  }
  return <section>
    <div className="settings-page-header">
      <div><h3>Native execution sandbox</h3><p>Windows AppContainer isolation and Job Object resource limits. No container runtime.</p></div>
    </div>
    <div className="settings-info-banner"><ShieldCheck size={15}/>
      <span>Commands run under restricted Windows permissions. Terminal permission still controls Allow / Ask / Deny. Never falls back to unrestricted execution.</span>
    </div>
    {error && <div className="settings-error-card" role="alert"><AlertCircle size={16}/>{error}</div>}
    <div className="settings-group-card">
      <div className="settings-row"><div className="settings-row__icon"><Terminal size={16}/></div>
        <div className="settings-row__body"><strong>Windows sandbox</strong><span>{status?.message ?? "Checking native sandbox…"}</span></div>
        <span className={`settings-status-pill ${status?.active ? "settings-status-pill--online" : "settings-status-pill--offline"}`}>{status?.active ? "Ready" : "Unavailable"}</span>
      </div>
      <div className="settings-row"><div className="settings-row__body"><strong>Resource and network protection</strong>
        <span>{status?.active ? "Filesystem restricted · Network policy enforced · CPU/RAM limits requested" : "Protection cannot be verified until native sandbox launches"}</span></div></div>
      {draft && <>
        <div className="settings-row"><div className="settings-row__body"><strong>Sandbox commands enabled</strong><span>Disable this to remove Deep Agents execute from future runs.</span></div>
          <SettingsToggle label="Enable native execution" checked={draft.enabled} disabled={busy} onChange={(enabled) => { setDraft({...draft,enabled});setSaved(false); }}/></div>
        <div className="settings-row"><label htmlFor="sandbox-memory">Memory limit</label>
          <select id="sandbox-memory" value={draft.memory_limit} disabled={busy} onChange={(event) => setDraft({...draft,memory_limit:event.target.value})}>
            <option value="256m">256 MB</option><option value="512m">512 MB</option><option value="1g">1 GB</option><option value="2g">2 GB</option><option value="4g">4 GB</option>
          </select></div>
        <div className="settings-row"><label htmlFor="sandbox-cpu">CPU limit</label>
          <select id="sandbox-cpu" value={draft.cpu_limit} disabled={busy} onChange={(event) => setDraft({...draft,cpu_limit:Number(event.target.value)})}>
            <option value={0.5}>0.5 core</option><option value={1}>1 core</option><option value={2}>2 cores</option><option value={4}>4 cores</option>
          </select></div>
        <div className="settings-row"><label htmlFor="sandbox-timeout">Timeout</label>
          <select id="sandbox-timeout" value={draft.timeout_seconds} disabled={busy} onChange={(event) => setDraft({...draft,timeout_seconds:Number(event.target.value)})}>
            <option value={30}>30 seconds</option><option value={60}>1 minute</option><option value={300}>5 minutes</option><option value={600}>10 minutes</option>
          </select></div>
        <div className="settings-row"><div className="settings-row__body"><strong>Internet access</strong><span>Off by default. Enable only to allow approved package downloads and outbound internet requests inside AppContainer.</span></div>
          <SettingsToggle label="Allow sandbox internet access" checked={draft.network_enabled} disabled={busy} onChange={(network_enabled) => setDraft({...draft,network_enabled})} /></div>
      </>}
    </div>
    <div className="desktop-actions"><button className="desktop-action-button" disabled={!backendOnline || busy} onClick={() => void refresh()}><RefreshCw size={15}/>Refresh status</button>
      <button className="desktop-action-button" disabled={!backendOnline || !draft || busy} onClick={() => void save()}><ShieldCheck size={15}/>Save sandbox settings</button></div>
    {saved && <div className="desktop-feedback" role="status"><CheckCircle2 size={15}/>Sandbox settings saved for future runs.</div>}
  </section>;
}
