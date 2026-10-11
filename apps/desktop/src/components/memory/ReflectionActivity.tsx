import { useEffect, useState } from "react";
import { Clock3 } from "lucide-react";
import { FriendlyEmpty, StatusPill } from "../LearningSurface";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { learningMemoryApi, type ReflectionSettings, type ReflectionStatus } from "../../lib/learning-memory-api";
import { chatApi } from "../../lib/api";

function ReflectionConfiguration({ busy }: { busy: boolean }) {
  const client = useQueryClient();
  const settings = useQuery({ queryKey: ["memory-center", "reflection-settings"], queryFn: learningMemoryApi.reflectionSettings });
  const catalog = useQuery({ queryKey: ["memory-center", "llm-catalog"], queryFn: chatApi.listModels });
  const [model, setModel] = useState("");
  const [enabled, setEnabled] = useState(true);
  const [daily, setDaily] = useState(20);
  const [tokens, setTokens] = useState(500);
  const [timeout, setTimeoutValue] = useState(45);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  // Hydrate the model ID (including provider-free aliases) when fetched.
  useEffect(() => {
    if (!settings.data) return;
    setModel(settings.data.model ?? "");
    setEnabled(settings.data.enabled);
    setDaily(settings.data.max_daily_reviews);
    setTokens(settings.data.max_output_tokens);
    setTimeoutValue(settings.data.timeout_seconds);
  }, [settings.data]);

  const modelOptions = [...new Set((catalog.data?.models ?? []).map((item) => item.id))];
  const validModel = !model || /^[a-zA-Z0-9._:/-]+$/.test(model);
  const validLimits = Number.isInteger(daily) && daily >= 0 && daily <= 1000
    && Number.isInteger(tokens) && tokens >= 100 && tokens <= 2000
    && Number.isInteger(timeout) && timeout >= 5 && timeout <= 180;

  function edit(changes: () => void) {
    changes();
    setSaved(false);
    setSaveError(null);
  }

  async function save() {
    if (busy || saving || !validModel || !validLimits) return;
    setSaving(true);
    setSaveError(null);
    setSaved(false);
    try {
      const persisted = await learningMemoryApi.setReflectionSettings({
        enabled, model: model.trim() || null, max_daily_reviews: daily,
        max_output_tokens: tokens, timeout_seconds: timeout,
      } satisfies Partial<ReflectionSettings>);
      // Never display Saved before the backend has confirmed persistence.
      client.setQueryData(["memory-center", "reflection-settings"], persisted);
      void client.invalidateQueries({ queryKey: ["memory-center", "reflection"] });
      setSaved(true);
    } catch (cause) {
      setSaveError(cause instanceof Error ? cause.message : "Could not save learning settings.");
    } finally {
      setSaving(false);
    }
  }

  if (settings.isLoading) return <p className="memory-center__note">Loading reflection configuration…</p>;
  if (settings.error) return <p role="alert" className="memory-center__warning">Could not load reflection configuration.</p>;
  return <details className="memory-center__advanced"><summary>Background learning settings <span>Model, daily budget, timeout</span></summary><section className="memory-center__configuration" aria-label="Reflection model and budget">
    <h4>Reflection model &amp; budget</h4>
    <p className="memory-center__note">Your selection is stored on the backend and restored when you return. Leave the model empty to use the global chat default.</p>
    <div className="memory-center__field-grid">
      <label><span>Background reviews</span><select value={String(enabled)} disabled={saving} onChange={(event) => edit(() => setEnabled(event.target.value === "true"))}><option value="true">Enabled</option><option value="false">Disabled</option></select></label>
      <label><span>Discovered Bifrost model</span><select value={modelOptions.includes(model) ? model : ""} disabled={saving || catalog.isLoading} onChange={(event) => edit(() => setModel(event.target.value))}>
        <option value="">{catalog.isLoading ? "Loading models…" : "Use global default / choose model…"}</option>
        {modelOptions.map((name) => <option value={name} key={name}>{name}</option>)}
      </select></label>
      <label><span>Review model</span><input aria-label="Reflection model" value={model} disabled={saving} maxLength={200} placeholder="Default chat model" onChange={(event) => edit(() => {
        setModel(event.target.value);
      })} /></label>
      <label><span>Daily review budget</span><input type="number" min="0" max="1000" value={daily} disabled={saving} onChange={(event) => edit(() => setDaily(Number(event.target.value)))} /></label>
      <label><span>Maximum output tokens</span><input type="number" min="100" max="2000" value={tokens} disabled={saving} onChange={(event) => edit(() => setTokens(Number(event.target.value)))} /></label>
      <label><span>Timeout (seconds)</span><input type="number" min="5" max="180" step="1" value={timeout} disabled={saving} onChange={(event) => edit(() => setTimeoutValue(Number(event.target.value)))} /></label>
    </div>
    {!validModel && <p className="memory-center__warning">Enter a Bifrost model ID or alias, or leave blank for the default.</p>}
    {saveError && <p className="memory-center__warning" role="alert">{saveError}</p>}
    {saved && <p className="memory-center__note" role="status">Learning settings saved. Your choices will be restored next time.</p>}
    <button type="button" className="memory-center__button memory-center__button--primary" disabled={busy || saving || !validModel || !validLimits} onClick={() => void save()}>{saving ? "Saving…" : "Save learning settings"}</button>
  </section></details>;
}

export function ReflectionActivity({ status, busy }: {
  status?: ReflectionStatus; busy: boolean;
  perform: (action: () => Promise<unknown>) => Promise<void>;
}) {
  return <div className="memory-center__records">
    <ReflectionConfiguration busy={busy} />
    <h4>Recent reviews</h4>
    <p className="memory-center__note">Task-level reflection happens in the background; verified candidates may be evaluated and promoted automatically.</p>
    <div className="memory-center__counts">{Object.entries(status?.counts ?? {}).map(([key, value]) => <span key={key}>{key.replaceAll("_", " ")}: <strong>{value}</strong></span>)}</div>
    {!status?.jobs.length && <FriendlyEmpty icon={Clock3} title="No background learning yet" description="Meaningful completed tasks can enter the review queue after the chat response." />}
    {status?.jobs.map((job) => <article className="memory-center__record" key={job.id}>
      <div className="memory-center__head"><strong>{job.reason === "feedback" ? "Feedback review" : "Experience review"}</strong><StatusPill status={job.status} /></div>
      <p className="memory-center__note">{new Date(job.updated_at).toLocaleString()} · Attempts: {job.attempts}{job.result?.model ? ` · ${job.result.model}` : ""}</p>
      {job.result?.summary && <p className="memory-center__preserve">{job.result.summary}</p>}
      {job.last_error && <p role="alert" className="memory-center__warning">Review error: {job.last_error}</p>}
      {job.result?.insights?.length ? <details><summary>Extracted observations ({job.result.insights.length})</summary><ul>{job.result.insights.map((insight, index) => <li key={index}>{insight.content} — {insight.kind}, evidence events {insight.evidence_event_seqs.join(", ")}</li>)}</ul></details> : null}
      {!!job.result?.skill_candidates?.length && <p className="memory-center__note">Discovered skill candidates: {job.result.skill_candidates.length}</p>}
      <details><summary>Technical details</summary><p className="memory-center__note">Logical task: <code>{job.task_id}</code></p></details>
    </article>)}
  </div>;
}
