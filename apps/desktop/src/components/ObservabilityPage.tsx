import { useQuery } from "@tanstack/react-query";
import { isTauri } from "@tauri-apps/api/core";
import { openUrl } from "@tauri-apps/plugin-opener";
import { Activity, ArrowUpRight, CheckCircle2, CircleAlert, Clock3, RefreshCw, ShieldCheck } from "lucide-react";
import { observabilityApi, type ObservabilitySummary } from "../lib/observability-api";
import { useChatStore } from "../stores/chat-store";

const readable = (n: number | null | undefined, suffix = "") => n == null ? "—" : `${n.toLocaleString(undefined, { maximumFractionDigits: 2 })}${suffix}`;
const date = (v: string) => Number.isNaN(Date.parse(v)) ? "Unknown" : new Date(v).toLocaleString();

async function openLocalPhoenix(url: string) {
  // Never use an externally controlled endpoint as a desktop opener target.
  if (url !== "http://127.0.0.1:6006") return;
  if (isTauri()) await openUrl(url);
  else window.open(url, "_blank", "noopener,noreferrer");
}

function Stat({ label, value, note }: { label: string; value: string; note?: string }) {
  return <div className="observe-stat"><small>{label}</small><strong>{value}</strong>{note && <span>{note}</span>}</div>;
}

export function ObservabilityPage({ backendOnline }: { backendOnline: boolean }) {
  const summary = useQuery({ queryKey: ["observability", "summary"], queryFn: observabilityApi.summary,
    enabled: backendOnline, refetchInterval: 15000 });
  const status = useQuery({ queryKey: ["observability", "status"], queryFn: observabilityApi.status,
    enabled: backendOnline, refetchInterval: 10000 });
  const setPage = useChatStore((state) => state.setPage);
  const data: ObservabilitySummary | undefined = summary.data;
  const connected = status.data?.connected ?? false;
  return <section className="observe-page" aria-label="Observability">
    <header className="observe-hero">
      <div className="observe-symbol"><Activity size={22}/></div>
      <div className="observe-headline"><h1>Observability</h1><p>Local agent diagnostics from recorded Trajecta runs</p></div>
      <button className="knowledge-button" type="button" disabled={!backendOnline}
        onClick={() => void Promise.all([summary.refetch(), status.refetch()])}><RefreshCw size={15}/> Refresh</button>
    </header>
    <div className="observe-connection">
      {connected ? <CheckCircle2 size={17}/> : <CircleAlert size={17}/>}
      <div><strong>Phoenix · {connected ? "Connected" : "Not connected"}</strong><p>
        {connected ? "Phoenix is reachable on localhost:6006." : "Run Phoenix locally or enable its managed process in Settings → Observability."}
        {status.data && !status.data.enabled && " Agent trace export is disabled."}
      </p></div>
      <button className="knowledge-button" disabled={!connected} type="button" onClick={() => void openLocalPhoenix("http://127.0.0.1:6006")}>Open Phoenix <ArrowUpRight size={15}/></button>
    </div>
    {!backendOnline && <p className="observe-message" role="alert">Trajecta backend is offline. Run metrics are not available.</p>}
    {(summary.isError || status.isError) && <p className="observe-message" role="alert">Diagnostics could not be loaded. Check the FastAPI backend log.</p>}
    <div className="observe-section-label">Performance <small>Based on the most recent {data?.sample_size ?? 0} saved trajectories</small></div>
    <div className="observe-stats">
      <Stat label="Recorded runs" value={readable(data?.sample_size)}/>
      <Stat label="Average latency" value={readable(data?.latency_seconds.average, "s")}/>
      <Stat label="p50 latency" value={readable(data?.latency_seconds.p50, "s")}/>
      <Stat label="p95 latency" value={readable(data?.latency_seconds.p95, "s")}/>
      <Stat label="p99 latency" value={readable(data?.latency_seconds.p99, "s")}/>
      <Stat label="p95 first token" value={readable(data?.ttft_seconds.p95, "s")}/>
    </div>
    <div className="observe-columns">
      <article className="observe-card"><h2>Execution & usage</h2><p>Technical completion is not verified task success.</p>
        <dl>
          <div><dt>Completed</dt><dd>{readable(data?.outcomes.completed)}</dd></div>
          <div><dt>Failed</dt><dd>{readable(data?.outcomes.failure)}</dd></div>
          <div><dt>Interrupted</dt><dd>{readable(data?.outcomes.interrupted)}</dd></div>
          <div><dt>Cancelled</dt><dd>{readable(data?.outcomes.cancelled)}</dd></div>
          <div><dt>Input / output tokens</dt><dd>{readable(data?.usage.input_tokens)} / {readable(data?.usage.output_tokens)}</dd></div>
          <div><dt>Tool calls / errors</dt><dd>{readable(data?.usage.tool_calls)} / {readable(data?.usage.tool_errors)}</dd></div>
          <div><dt>Estimated cost</dt><dd>Not recorded</dd></div>
        </dl>
      </article>
      <article className="observe-card"><h2>Instrumented operations</h2><p>Inspect detailed spans in Phoenix rather than duplicating its trace viewer.</p>
        <div className="observe-feature"><ShieldCheck size={18}/><span>Guardrail inspections and cache decisions</span></div>
        <div className="observe-feature"><Activity size={18}/><span>Memory retrieval and skill operations</span></div>
        <div className="observe-feature"><Clock3 size={18}/><span>Background-learning jobs and tool recovery</span></div>
        <p className="observe-muted">These details are available only for operations that were traced. The summary above reads saved runtime counters, not Phoenix spans.</p>
        <button type="button" className="knowledge-button" onClick={() => setPage("knowledge")}>Open learning & memory diagnostics</button>
      </article>
    </div>
    <div className="observe-section-label">Recent runs <small>Latest 30 executions</small></div>
    <div className="observe-card observe-runs">
      {!data?.recent.length && <p className="observe-muted">No recorded runs yet.</p>}
      {data?.recent.map((run) => <div key={run.trajectory_id} className="observe-run">
        <div><strong>{run.outcome}</strong><small>{date(run.created_at)}</small></div>
        <span>{readable(run.duration_seconds, "s")}</span>
        {run.trace_id ? <>
          {connected && status.data?.project_id && <button type="button" className="knowledge-button"
            onClick={() => { const id = status.data!.project_id!;
              const url = `http://127.0.0.1:6006/projects/${encodeURIComponent(id)}/traces/${run.trace_id}`;
              if (isTauri()) void openUrl(url); else window.open(url, "_blank", "noopener,noreferrer");
            }}>View trace <ArrowUpRight size={14}/></button>}
          <button type="button" className="knowledge-button" onClick={() => void navigator.clipboard.writeText(run.trace_id!)} title="Copy trace ID to find in Phoenix">Copy ID</button>
          </> : <small>Not traced</small>}
      </div>)}
    </div>
    <p className="observe-footnote">Metrics are sampled from saved Trajecta trajectories (up to 500), not a global Phoenix aggregation. This view never exposes prompts, tool arguments, memory text, or private outputs. For individual spans open Phoenix and locate a trace using its copied ID.</p>
  </section>;
}
