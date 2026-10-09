import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { learningMemoryApi, type ProcedureDraft } from "../../lib/learning-memory-api";
import { useChatStore } from "../../stores/chat-store";
import type { SkillRegistryItem } from "../../types/settings";

function Record({ draft, archived, busy, workspacePath, perform }: {
  draft: ProcedureDraft; archived: boolean; busy: boolean; workspacePath: string | null;
  perform: (action: () => Promise<unknown>) => Promise<void>;
}) {
  const [historyOpen, setHistoryOpen] = useState(false);
  const [candidateName, setCandidateName] = useState("");
  const history = useQuery({
    queryKey: ["memory-center", "procedure-history", draft.id, workspacePath],
    queryFn: () => learningMemoryApi.history(draft.id, workspacePath),
    enabled: historyOpen,
  });
  const canCandidate = draft.status === "approved" && draft.user_confirmed && !workspacePath && draft.steps.every((step) => step.outcome === "observed_result");
  return <article className="memory-center__record">
    <div className="memory-center__head"><strong>{draft.title}</strong><span className="memory-center__status">{archived ? "Archived" : draft.status.replaceAll("_", " ")}</span></div>
    <p className="memory-center__note">{draft.kind === "revision" ? `Suggested revision of ${draft.target_skill_name} v${draft.base_skill_version}` : "New procedure suggestion"} · Draft v{draft.version} · {draft.user_confirmed ? "User-confirmed outcome" : "Unconfirmed outcome"}</p>
    <p className="memory-center__preserve">{draft.rationale}</p>
    <details><summary>Observed workflow ({draft.steps.length} steps)</summary><ol>{draft.steps.map((step, index) => <li key={`${step.event_seq}-${index}`}><code>{step.tool}</code> — {step.outcome} (event {step.event_seq}{step.result_seq !== null ? `, result ${step.result_seq}` : ""})</li>)}</ol>
      <p className="memory-center__note">Source trajectory: <code>{draft.source_trajectory_id}</code>. Raw arguments are not stored.</p>
      {draft.evidence.risk_review_required && <p className="memory-center__warning">Includes tools requiring permission review.</p>}
    </details>
    <div className="memory-center__actions">
      {draft.status === "needs_review" && !archived && <>
        <button type="button" disabled={busy} className="memory-center__button" onClick={() => void perform(() => learningMemoryApi.review(draft.id, "reject", workspacePath))}>Reject</button>
        <button type="button" disabled={busy} className="memory-center__button memory-center__button--primary" onClick={() => void perform(() => learningMemoryApi.review(draft.id, "approve", workspacePath))}>Approve suggestion</button>
        <button type="button" disabled={busy} className="memory-center__button" onClick={() => {
          if (window.confirm("Archive this pending suggestion? You can restore it later.")) void perform(() => learningMemoryApi.archive(draft.id, workspacePath));
        }}>Archive</button>
      </>}
      {archived && <button type="button" disabled={busy} className="memory-center__button" onClick={() => void perform(() => learningMemoryApi.restore(draft.id, workspacePath))}>Restore</button>}
      <button type="button" className="memory-center__button" onClick={() => setHistoryOpen(!historyOpen)}>{historyOpen ? "Hide" : "Show"} revisions</button>
    </div>
    {draft.status === "approved" && !archived && <div className="memory-center__feedback">
      <p className="memory-center__note">Approval does not activate a skill. A candidate still requires explicit creation and evaluation.</p>
      {canCandidate ? <div className="memory-center__actions">
        {!draft.target_skill_name && <label>New skill name<input aria-label={`Skill name for ${draft.title}`} value={candidateName} onChange={(event) => setCandidateName(event.target.value)} placeholder="my-repeatable-workflow" maxLength={64} /></label>}
        <button type="button" className="memory-center__button memory-center__button--primary" disabled={busy || (!draft.target_skill_name && !/^[a-z][a-z0-9-]{2,63}$/.test(candidateName))} onClick={() => {
          if (window.confirm("Create an unevaluated skill candidate from this procedure?")) void perform(() => learningMemoryApi.candidate(draft.id, draft.target_skill_name ? null : candidateName, workspacePath));
        }}>Create candidate for evaluation</button>
      </div> : <p className="memory-center__warning">Candidate creation requires positive user feedback, a general (non-workspace) scope, and successful observed steps.</p>}
    </div>}
    {draft.status === "candidate_created" && <p className="memory-center__note">Candidate {draft.candidate_id} created. Evaluate it in Skills before activation.</p>}
    {historyOpen && <div className="memory-center__history">{history.isLoading ? "Loading revisions…" : history.error ? "Unable to load revision history." : history.data?.items.length ? history.data.items.map((record) => <details key={record.version}><summary>Revision {record.version} · {record.created_at}</summary><p className="memory-center__preserve">{record.snapshot.rationale}</p></details>) : "No earlier revisions."}</div>}
  </article>;
}

export function ProceduralMemories({ items, archived, activeSkills, search, busy, workspacePath, perform }: {
  items: ProcedureDraft[]; archived: ProcedureDraft[]; activeSkills: SkillRegistryItem[]; search: string; busy: boolean; workspacePath: string | null;
  perform: (action: () => Promise<unknown>) => Promise<void>;
}) {
  const [filter, setFilter] = useState<"current" | "archived">("current");
  const setPage = useChatStore((s) => s.setPage);
  const source = filter === "archived" ? archived : items;
  const displayed = source.filter((draft) => `${draft.title} ${draft.rationale} ${draft.target_skill_name ?? ""}`.toLowerCase().includes(search.toLowerCase()));
  return <div className="memory-center__records">
    <p className="memory-center__note">Procedural proposals are drafts, not executable skills. Only approved, versioned skills may run.</p>
    {!workspacePath && <section className="memory-center__skill-list" aria-label="Active procedural skills">
      <div className="memory-center__head"><h4>Active skills ({activeSkills.length})</h4><button type="button" className="memory-center__button" onClick={() => setPage("skills")}>Manage skills</button></div>
      {activeSkills.length ? activeSkills.map((skill) => <div className="memory-center__skill" key={skill.id}><strong>{skill.name}</strong><span>Version {skill.version} · active</span></div>) : <p className="memory-center__note">No active skills in the registry.</p>}
    </section>}
    <div className="memory-center__actions"><button type="button" className={`memory-center__button ${filter === "current" ? "memory-center__button--primary" : ""}`} onClick={() => setFilter("current")}>Current ({items.length})</button>
      <button type="button" className={`memory-center__button ${filter === "archived" ? "memory-center__button--primary" : ""}`} onClick={() => setFilter("archived")}>Archived ({archived.length})</button></div>
    {displayed.length === 0 && <div className="settings-empty-state settings-empty-state--small"><strong>No {filter} procedures</strong><span>Reflection and explicit feedback create reviewable suggestions from observed tool sequences.</span></div>}
    {displayed.map((draft) => <Record key={draft.id} draft={draft} archived={filter === "archived"} workspacePath={workspacePath} busy={busy} perform={perform} />)}
  </div>;
}
