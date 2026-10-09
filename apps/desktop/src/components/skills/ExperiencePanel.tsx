import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Archive, BookOpen, Check, ChevronRight, Clock3, History, Lightbulb,
  RefreshCw, Search, ShieldAlert, Sparkles, X, CheckCircle2, type LucideIcon,
} from "lucide-react";
import { learningApi, settingsApi, type LearnedExperience, type LearningOverview } from "../../lib/api";
import type { SkillCandidate } from "../../types/settings";
import { DashboardMetric, DashboardSection, FriendlyEmpty, LearningFlow, StatusPill } from "../LearningSurface";
import { SettingsToggle } from "../settings/SettingsToggle";
import { VersionHistory } from "./VersionHistory";

type View = "overview" | "learned" | "improved" | "review" | "skills" | "rejected";
const EMPTY: LearningOverview = { items: [], skills: [], previous_candidates: [] };
const VIEWS: { key: View; label: string; hint: string; icon: LucideIcon }[] = [
  { key: "overview", label: "Overview", hint: "What has changed", icon: Sparkles },
  { key: "learned", label: "Learned", hint: "Saved knowledge", icon: Lightbulb },
  { key: "improved", label: "Improvements", hint: "Updated knowledge", icon: History },
  { key: "review", label: "Needs review", hint: "Your decisions", icon: ShieldAlert },
  { key: "skills", label: "Saved skills", hint: "Reusable capabilities", icon: BookOpen },
  { key: "rejected", label: "Rejected", hint: "Archived decisions", icon: Archive },
];

function hasManualRejection(item: SkillCandidate): boolean {
  return item.status === "rejected" && typeof item.metadata?.rejection_reason === "string"
    && item.metadata.rejection_reason.trim().length > 0;
}
function pendingLegacy(item: SkillCandidate): boolean {
  // Evaluation failures are not user rejections; retain those drafts for review.
  return !hasManualRejection(item) && item.status !== "promoted" && item.status !== "archived";
}
function toTitle(label: string): string { return label.replaceAll("_", " "); }
function readableDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "Date unavailable" : date.toLocaleDateString();
}
function emptyMessage(view: View, searching: boolean): string {
  if (searching) return "Try a different keyword or clear the search.";
  switch (view) {
    case "overview": return "Learning insights will appear once Trajecta records experience.";
    case "learned": return "Trajecta will save explicit preferences, corrections, and confirmed procedures here.";
    case "improved": return "When saved knowledge is revised, its updated version appears here.";
    case "review": return "Corrections and skill drafts requiring a decision will appear here.";
    case "rejected": return "Rejected knowledge stays available for audit instead of being silently deleted.";
    case "skills": return "Reusable skills that are saved in the registry will appear here.";
  }
}

function CandidateApprovalDialog({ candidate, busy, onClose, onApprove, approvalError }: {
  candidate: SkillCandidate | null;
  busy: boolean;
  onClose: () => void;
  onApprove: (candidate: SkillCandidate) => Promise<void>;
  approvalError: string | null;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [confirmed, setConfirmed] = useState(false);
  useEffect(() => {
    setConfirmed(false);
    const current = dialog.current;
    if (!current) return;
    if (candidate && !current.open) current.showModal();
    else if (!candidate && current.open) current.close();
  }, [candidate]);
  return <dialog className="knowledge-review-dialog" ref={dialog} onClose={onClose} onCancel={(event) => { if (busy) event.preventDefault(); }}>
    {candidate && <div className="knowledge-review-dialog__body">
      <div className="knowledge-review-dialog__top"><div><span className="knowledge-header__eyebrow">SKILL APPROVAL</span>
        <h3>Review before activating</h3></div><button type="button" className="knowledge-icon-button" disabled={busy} onClick={onClose} aria-label="Close review"><X size={18} /></button></div>
      <p>Only read-only candidates are eligible for this manual approval flow. The server enforces eligibility, and tool permissions still apply.</p>
      <h4>{candidate.name}</h4><p>{candidate.description}</p>
      <div className="knowledge-review-dialog__instructions"><strong>Full skill instructions</strong><pre>{candidate.content}</pre></div>
      {approvalError && <p className="memory-center__warning" role="alert">{approvalError}</p>}
      <label className="knowledge-review-dialog__ack"><input type="checkbox" checked={confirmed} disabled={busy} onChange={(event) => setConfirmed(event.target.checked)} /> I have reviewed the complete instructions and understand this enables a reusable skill without replay evaluation.</label>
      <div className="knowledge-review-dialog__actions"><button type="button" className="knowledge-button" disabled={busy} onClick={onClose}>Cancel</button>
        <button type="button" className="knowledge-button knowledge-button--primary" disabled={!confirmed || busy} onClick={() => void onApprove(candidate)}>
          {busy ? <RefreshCw className="settings-spin" size={15} /> : <Check size={15} />} Activate eligible skill</button></div>
    </div>}
  </dialog>;
}

export function ExperiencePanel({ enabled, view, compact = false }: { enabled: boolean; view?: View; compact?: boolean }) {
  const [localTab, setTab] = useState<View>("overview");
  const tab = view ?? localTab;
  const [search, setSearch] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [historyFor, setHistoryFor] = useState<string | null>(null);
  const [candidateForApproval, setCandidateForApproval] = useState<SkillCandidate | null>(null);
  const [error, setError] = useState<string | null>(null);
  const client = useQueryClient();
  const { data: overview = EMPTY, isLoading: loading, error: loadError, refetch } = useQuery({
    queryKey: ["learning", "overview"],
    queryFn: learningApi.overview,
    enabled,
  });
  async function reload(): Promise<void> {
    await client.invalidateQueries({ queryKey: ["learning", "overview"] });
  }

  const pending = useMemo(() => overview.previous_candidates.filter(pendingLegacy), [overview]);
  const legacyRejected = useMemo(() => overview.previous_candidates.filter(hasManualRejection), [overview]);
  const counts: Record<View, number> = {
    overview: overview.items.length + overview.skills.length,
    learned: overview.items.filter((x) => x.status === "active").length,
    improved: overview.items.filter((x) => x.version > 1 && x.status === "active").length,
    review: overview.items.filter((x) => x.status === "needs_review").length + pending.length,
    rejected: overview.items.filter((x) => x.status === "rejected").length + legacyRejected.length,
    skills: overview.skills.length,
  };
  const navigate = (view: View) => { setTab(view); setSearch(""); setError(null); };
  const needle = search.trim().toLowerCase();
  const matchingExperience = (item: LearnedExperience) => !needle || `${item.kind} ${item.content}`.toLowerCase().includes(needle);
  const matchingCandidate = (item: SkillCandidate) => !needle || `${item.name} ${item.description} ${item.content}`.toLowerCase().includes(needle);
  const shownExperiences = overview.items.filter((item) => {
    if (tab === "overview" || tab === "skills") return false;
    if (tab === "learned" && item.status !== "active") return false;
    if (tab === "improved" && (item.version <= 1 || item.status !== "active")) return false;
    if (tab === "review" && item.status !== "needs_review") return false;
    if (tab === "rejected" && item.status !== "rejected") return false;
    return matchingExperience(item);
  });
  const shownCandidates = (tab === "review" ? pending : tab === "rejected" ? legacyRejected : []).filter(matchingCandidate);
  const shownSkills = tab === "skills" ? overview.skills.filter((skill) => !needle || skill.name.toLowerCase().includes(needle)) : [];
  const hasResults = shownExperiences.length + shownCandidates.length + shownSkills.length > 0;
  const activeSkills = overview.skills.filter((skill) => skill.status === "active").length;
  const selected = VIEWS.find((view) => view.key === tab)!;

  async function act(key: string, operation: () => Promise<unknown>): Promise<boolean> {
    if (busy) return false;
    setBusy(key); setError(null);
    try { await operation(); await reload(); return true; }
    catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); return false; }
    finally { setBusy(null); }
  }
  async function approveCandidate(candidate: SkillCandidate) {
    if (await act(candidate.id, () => learningApi.approveCandidate(candidate.id))) {
      setCandidateForApproval(null);
    }
  }

  return <section className={compact ? "knowledge-embedded learning-workspace" : "knowledge-layout learning-workspace"} aria-label="Learnings and skills">
    {!compact && <header className="knowledge-header"><div><div className="knowledge-header__eyebrow"><Sparkles size={16} /> SKILLS & LEARNING</div>
      <h3>How Trajecta gets better</h3>
      <p>Explore what it has learned from conversations, review suggested improvements, and manage reusable skills.</p>
    </div><button type="button" className="knowledge-button" title="Refresh learning records" disabled={loading || busy !== null} onClick={() => { setError(null); void refetch(); }}>
      <RefreshCw size={16} className={loading ? "settings-spin" : ""} /> Refresh
    </button></header>}

    {!compact && <nav className="knowledge-navigation" aria-label="Learning sections">
      {VIEWS.map(({ key, label, hint, icon: Icon }) => <button key={key} type="button"
        className={key === tab ? "knowledge-navigation__item is-active" : "knowledge-navigation__item"}
        aria-current={key === tab ? "page" : undefined} onClick={() => navigate(key)}><Icon size={18} aria-hidden="true" />
        <span><strong>{label}</strong><small>{hint}</small></span>
        {key === "review" && counts.review > 0 && <em className="knowledge-navigation__count">{counts.review}</em>}
        {key === tab && <ChevronRight size={16} className="knowledge-navigation__chevron" aria-hidden="true" />}
      </button>)}
    </nav>}

    {(error || loadError) && <div className="settings-error-card" role="alert"><ShieldAlert size={17} /> {error ?? (loadError instanceof Error ? loadError.message : "Could not load learning records.")}
      <button type="button" className="knowledge-button" onClick={() => { setError(null); void refetch(); }}>Retry</button></div>}
    {loading ? <div className="knowledge-loading"><RefreshCw size={18} className="settings-spin" />Loading learning records…</div>
      : !loadError || overview !== EMPTY ? <>
        {tab === "overview" && <div className="knowledge-overview">
          <p className="knowledge-overview__note">Learning counts use the most recent records returned by Trajecta (up to 200 experiences and 200 candidate drafts).</p>
          <div className="knowledge-metrics">
            <DashboardMetric label="Learned" value={counts.learned} description="Active knowledge entries" icon={Lightbulb} onClick={() => navigate("learned")} />
            <DashboardMetric label="Improvements" value={counts.improved} description="Updated knowledge entries" icon={History} onClick={() => navigate("improved")} />
            <DashboardMetric label="Needs review" value={counts.review} description="Knowledge & candidate drafts" icon={ShieldAlert} attention={counts.review > 0} onClick={() => navigate("review")} />
            <DashboardMetric label="Active skills" value={activeSkills} description="Enabled for reuse" icon={BookOpen} onClick={() => navigate("skills")} />
          </div>
          <div className="knowledge-overview__columns">
            <DashboardSection title="What needs your attention" description="Nothing gets approved simply because it was learned.">
              {counts.review > 0 ? <div className="knowledge-action-list">
                <button type="button" onClick={() => navigate("review")}><ShieldAlert size={19} /><span><strong>{counts.review} items awaiting review</strong>
                  <small>Inspect content and decide whether to approve or reject.</small></span><ChevronRight size={17} /></button>
              </div> : <div className="knowledge-all-clear"><CheckCircle2 size={21} /><div><strong>You're all caught up</strong><p>Nothing currently needs your approval.</p></div></div>}
              {counts.rejected > 0 && <button type="button" className="knowledge-inline-link" onClick={() => navigate("rejected")}>View {counts.rejected} rejected records <ChevronRight size={14} /></button>}
            </DashboardSection>
            <LearningFlow variant="skill" />
          </div>
          <DashboardSection title="Explore learning" description="Understand the difference between saved knowledge and executable skills.">
            <div className="knowledge-guide-grid">
              <button type="button" onClick={() => navigate("learned")}><Lightbulb size={19} /><strong>Learned knowledge</strong><span>Preferences, corrections, and successful procedures</span></button>
              <button type="button" onClick={() => navigate("improved")}><History size={19} /><strong>Improvements</strong><span>Saved knowledge refined over time</span></button>
              <button type="button" onClick={() => navigate("skills")}><BookOpen size={19} /><strong>Reusable skills</strong><span>Versioned capabilities you can enable or disable</span></button>
            </div>
          </DashboardSection>
        </div>}
        {tab !== "overview" && <div className="knowledge-content">
          <div className="knowledge-content__heading"><div><h4>{compact && tab === "learned" ? "Learned knowledge" : selected.label} <span className="knowledge-content__count">{counts[tab]}</span></h4><p>{compact && tab === "learned" ? "Preferences, corrections and refinements captured from previous work." : selected.hint}</p></div>
            <label className="knowledge-search"><Search size={17} /><input value={search} onChange={(event) => setSearch(event.target.value)}
              placeholder={`Search ${selected.label.toLowerCase()}…`} aria-label="Search saved knowledge" /></label>
          </div>
          {!hasResults ? <FriendlyEmpty icon={selected.icon} title={needle ? "No matching records" : "Nothing here yet"} description={emptyMessage(tab, Boolean(needle))} />
            : <div className="learning-workspace__records">
              {shownExperiences.map((item) => <article className="learning-workspace__record" key={item.id}>
                <div className="learning-workspace__record-head"><span className="learning-workspace__tag"><Lightbulb size={15} /> {toTitle(item.kind)}</span>
                  <StatusPill status={item.status} /></div>
                <p className="learning-workspace__content">{item.content}</p>
                <div className="learning-workspace__record-bottom"><span><Clock3 size={14} /> Updated {readableDate(item.updated_at)} · {item.version > 1 ? `Refined (v${item.version})` : "First version"}</span>
                  {tab === "review" && <div className="learning-workspace__actions"><button disabled={busy !== null} type="button" onClick={() => void act(item.id, () => learningApi.review(item.id, "reject"))}>Reject</button>
                    <button className="learning-workspace__primary" disabled={busy !== null} type="button" onClick={() => void act(item.id, () => learningApi.review(item.id, "approve"))}>
                      {busy === item.id ? <RefreshCw size={14} className="settings-spin" /> : <Check size={14} />} Approve learning</button></div>}
                </div>
                <details className="learning-workspace__details"><summary>Source and technical details</summary><div className="knowledge-record-meta">
                  <span>Model confidence: {Number.isFinite(item.confidence) ? `${Math.round(item.confidence * 100)}%` : "Unavailable"} (not a verified success rate)</span>
                  <span>Source trajectory: {item.source_trajectory_id ?? "Not recorded"}</span>
                  {Object.keys(item.evidence ?? {}).length > 0 && <pre>{JSON.stringify(item.evidence, null, 2)}</pre>}
                </div></details>
              </article>)}
              {shownCandidates.map((candidate) => <article className="learning-workspace__record" key={`previous-${candidate.id}`}>
                <div className="learning-workspace__record-head"><span className="learning-workspace__tag"><Archive size={15} /> Previous skill draft</span><StatusPill status={candidate.status} /></div>
                <h5 className="knowledge-record-title">{candidate.name}</h5><p className="learning-workspace__description">{candidate.description}</p>
                <details className="learning-workspace__details"><summary>View full skill instructions and evidence</summary><pre>{candidate.content}</pre>
                  <p>Source trajectories: {candidate.source_trajectory_ids.join(", ") || "None recorded"}</p></details>
                {tab === "review" && <div className="learning-workspace__actions learning-workspace__actions--end">
                  <button type="button" disabled={busy !== null} onClick={() => void act(candidate.id, () => settingsApi.rejectSkill(candidate.id, "Rejected by user from learning review"))}>Reject draft</button>
                  {candidate.status === "candidate" && <button type="button" className="learning-workspace__primary" disabled={busy !== null} onClick={() => { setError(null); setCandidateForApproval(candidate); }}><Check size={14} /> Review for activation</button>}
                </div>}
              </article>)}
              {shownSkills.map((skill) => <article className="learning-workspace__record" key={skill.id}>
                <div className="learning-workspace__record-head"><span className="learning-workspace__tag"><BookOpen size={15} /> Reusable skill</span><StatusPill status={skill.status} /></div>
                <h5 className="knowledge-record-title">{skill.name}</h5>
                <p className="learning-workspace__description">Version {skill.version} · Saved {readableDate(skill.created_at)}</p>
                <div className="learning-workspace__record-bottom"><span>Enable or disable this skill for future use.</span>
                  <SettingsToggle label={`Enable ${skill.name}`} checked={skill.status === "active"} disabled={busy !== null} onChange={(isEnabled) =>
                    void act(skill.id, () => settingsApi.setSkillEnabled(skill.name, isEnabled))} /></div>
                <details className="learning-workspace__details" onToggle={(event) => {
                  if (event.currentTarget.open) setHistoryFor(skill.name);
                  else if (historyFor === skill.name) setHistoryFor(null);
                }}><summary>Version history</summary>{historyFor === skill.name && <VersionHistory skillName={skill.name} />}</details>
              </article>)}
            </div>}
        </div>}
      </> : null}
    <CandidateApprovalDialog candidate={candidateForApproval} busy={candidateForApproval !== null && busy === candidateForApproval.id}
      onClose={() => { if (busy === null) setCandidateForApproval(null); }} onApprove={approveCandidate} approvalError={candidateForApproval ? error : null} />
  </section>;
}
