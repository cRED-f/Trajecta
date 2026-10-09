import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { learningMemoryApi, type ReflectionSettings, type ReflectionStatus } from "../../lib/learning-memory-api";
import { settingsApi } from "../../lib/api";

function ReflectionConfiguration({ busy, perform }: { busy: boolean; perform: (action: () => Promise<unknown>) => Promise<void> }) {
  const settings = useQuery({ queryKey: ["memory-center", "reflection-settings"], queryFn: learningMemoryApi.reflectionSettings });
  const catalog = useQuery({ queryKey: ["memory-center", "llm-catalog"], queryFn: settingsApi.llmCatalog });
  const [model, setModel] = useState("");
  const [enabled, setEnabled] = useState(true);
  const [daily, setDaily] = useState(20);
  const [tokens, setTokens] = useState(500);
  const [timeout, setTimeoutValue] = useState(45);
  const [provider, setProvider] = useState("");
  useEffect(() => {
    if (!settings.data) return;
    setModel(settings.data.model ?? "");
    setEnabled(settings.data.enabled);
    setDaily(settings.data.max_daily_reviews);
    setTokens(settings.data.max_output_tokens);
    setTimeoutValue(settings.data.timeout_seconds);
  }, [settings.data]);
  const models = useQuery({ queryKey: ["memory-center", "model-options", provider], queryFn: () => settingsApi.llmProviderModels(provider), enabled: !!provider });
  const validModel = !model || /^[a-zA-Z0-9._-]+\/[a-zA-Z0-9._:/-]+$/.test(model);
  if (settings.isLoading) return <p className="memory-center__note">Loading reflection configuration…</p>;
  if (settings.error) return <p role="alert" className="memory-center__warning">Could not load reflection configuration.</p>;
  return <section className="memory-center__configuration" aria-label="Reflection model and budget">
    <h4>Reflection model & budget</h4>
    <p className="memory-center__note">Reflections run through Bifrost after the chat response. Use a configured provider/model or follow the global default.</p>
    <div className="memory-center__field-grid">
      <label><span>Background reviews</span><select value={String(enabled)} onChange={(event) => setEnabled(event.target.value === "true")}><option value="true">Enabled</option><option value="false">Disabled</option></select></label>
      <label><span>Provider shortcut</span><select value={provider} onChange={(event) => setProvider(event.target.value)}><option value="">Choose provider…</option>{catalog.data?.providers.filter((p) => p.configured).map((p) => <option value={p.id} key={p.id}>{p.id}</option>)}</select></label>
      <label><span>Available model shortcut</span><select value="" disabled={!provider || models.isLoading || !models.data?.models.length} onChange={(event) => setModel(event.target.value)}><option value="">{models.isLoading ? "Loading…" : "Choose model…"}</option>{models.data?.models.map((name) => <option value={name.includes("/") ? name : `${provider}/${name}`} key={name}>{name}</option>)}</select></label>
      <label><span>Review model</span><input aria-label="Reflection model" value={model} maxLength={200} placeholder="Default chat model" onChange={(event) => setModel(event.target.value)} /></label>
      <label><span>Daily review budget</span><input type="number" min="0" max="1000" value={daily} onChange={(event) => setDaily(Number(event.target.value))} /></label>
      <label><span>Maximum output tokens</span><input type="number" min="100" max="2000" value={tokens} onChange={(event) => setTokens(Number(event.target.value))} /></label>
      <label><span>Timeout (seconds)</span><input type="number" min="5" max="180" step="1" value={timeout} onChange={(event) => setTimeoutValue(Number(event.target.value))} /></label>
    </div>
    {!validModel && <p className="memory-center__warning">Enter a qualified model, such as provider/model, or leave blank for the default.</p>}
    <button type="button" className="memory-center__button memory-center__button--primary" disabled={busy || !validModel || !Number.isInteger(daily) || daily < 0 || daily > 1000 || !Number.isInteger(tokens) || tokens < 100 || tokens > 2000 || timeout < 5 || timeout > 180} onClick={() => void perform(() => learningMemoryApi.setReflectionSettings({ enabled, model: model.trim() || null, max_daily_reviews: daily, max_output_tokens: tokens, timeout_seconds: timeout } satisfies Partial<ReflectionSettings>))}>Save learning settings</button>
  </section>;
}

export function ReflectionActivity({ status, busy, perform }: {
  status?: ReflectionStatus; busy: boolean;
  perform: (action: () => Promise<unknown>) => Promise<void>;
}) {
  return <div className="memory-center__records">
    <ReflectionConfiguration busy={busy} perform={perform} />
    <h4>Recent reviews</h4>
    <p className="memory-center__note">Review jobs are recorded after meaningful interactions. No evaluation or skill activation happens here.</p>
    <div className="memory-center__counts">{Object.entries(status?.counts ?? {}).map(([key, value]) => <span key={key}>{key.replaceAll("_", " ")}: <strong>{value}</strong></span>)}</div>
    {!status?.jobs.length && <div className="settings-empty-state settings-empty-state--small"><strong>No reflection activity yet</strong><span>Meaningful completed tasks will enter the review queue.</span></div>}
    {status?.jobs.map((job) => <article className="memory-center__record" key={job.id}>
      <div className="memory-center__head"><strong>{job.reason === "feedback" ? "Feedback review" : "Experience review"}</strong><span className="memory-center__status">{job.status}</span></div>
      <p className="memory-center__note">{new Date(job.updated_at).toLocaleString()} · Attempts: {job.attempts}{job.result?.model ? ` · ${job.result.model}` : ""}</p>
      {job.result?.summary && <p className="memory-center__preserve">{job.result.summary}</p>}
      {job.last_error && <p role="alert" className="memory-center__warning">Review error: {job.last_error}</p>}
      {job.result?.insights?.length ? <details><summary>Extracted observations ({job.result.insights.length})</summary><ul>{job.result.insights.map((insight, index) => <li key={index}>{insight.content} — {insight.kind}, evidence events {insight.evidence_event_seqs.join(", ")}</li>)}</ul></details> : null}
      {job.result?.procedure_draft_id && <p className="memory-center__note">Created reviewable procedure: {job.result.procedure_draft_id}</p>}
      <p className="memory-center__note">Source trajectory: <code>{job.trajectory_id}</code></p>
    </article>)}
  </div>;
}
