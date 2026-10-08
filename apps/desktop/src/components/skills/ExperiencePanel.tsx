import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Archive,
  BookOpen,
  Check,
  Clock3,
  Lightbulb,
  RefreshCw,
  Search,
  ShieldAlert,
  Sparkles,
} from "lucide-react";
import {
  learningApi,
  settingsApi,
  type LearnedExperience,
  type LearningOverview,
} from "../../lib/api";
import type { SkillCandidate } from "../../types/settings";
import { SettingsToggle } from "../settings/SettingsToggle";
import { VersionHistory } from "./VersionHistory";

type View = "learned" | "improved" | "review" | "rejected" | "skills";

const EMPTY: LearningOverview = {
  items: [], skills: [], previous_candidates: [],
};

const LABELS: Record<View, string> = {
  learned: "Learned",
  improved: "Improved",
  review: "Needs review",
  rejected: "Rejected",
  skills: "Saved skills",
};

function hasManualRejection(item: SkillCandidate): boolean {
  return item.status === "rejected" &&
    typeof item.metadata?.rejection_reason === "string" &&
    item.metadata.rejection_reason.trim().length > 0;
}

function pendingLegacy(item: SkillCandidate): boolean {
  // A failed automated evaluation was not a user's decision to reject.
  // Keep those drafts accessible for review rather than discarding them.
  return !hasManualRejection(item) &&
    item.status !== "promoted" && item.status !== "archived";
}

function titleCase(label: string): string {
  return label.replaceAll("_", " ");
}

function emptyMessage(view: View, searching: boolean): string {
  if (searching) return "No matching records.";
  switch (view) {
    case "learned": return "Trajecta will save explicit preferences and confirmed task procedures here.";
    case "improved": return "When you refine something Trajecta has learned, its updated version will appear here.";
    case "review": return "Corrections and procedures requiring your approval will appear here.";
    case "rejected": return "Rejected knowledge is archived here rather than silently deleted.";
    case "skills": return "Reusable skills you activate will appear here.";
  }
}

export function ExperiencePanel({ enabled }: { enabled: boolean }) {
  const [overview, setOverview] = useState<LearningOverview>(EMPTY);
  const [tab, setTab] = useState<View>("learned");
  const [search, setSearch] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [historyFor, setHistoryFor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refetchIndex, setRefetchIndex] = useState(0);

  const reload = useCallback(async () => {
    const response = await learningApi.overview();
    setOverview(response);
  }, []);

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    setLoading(true);
    learningApi.overview().then((response) => {
      if (!cancelled) { setOverview(response); setError(null); }
    }).catch((cause: unknown) => {
      if (!cancelled) setError(cause instanceof Error ? cause.message : String(cause));
    }).finally(() => {
      if (!cancelled) setLoading(false);
    });
    return () => { cancelled = true; };
  }, [enabled, refetchIndex]);

  const pending = useMemo(() => overview.previous_candidates.filter(pendingLegacy), [overview]);
  const legacyRejected = useMemo(() => overview.previous_candidates.filter(hasManualRejection), [overview]);
  const counts: Record<View, number> = {
    learned: overview.items.filter((x) => x.status === "active").length,
    improved: overview.items.filter((x) => x.version > 1 && x.status === "active").length,
    review: overview.items.filter((x) => x.status === "needs_review").length + pending.length,
    rejected: overview.items.filter((x) => x.status === "rejected").length + legacyRejected.length,
    skills: overview.skills.length,
  };

  const needle = search.trim().toLowerCase();
  const matchingExperience = (item: LearnedExperience) =>
    !needle || `${item.kind} ${item.content}`.toLowerCase().includes(needle);
  const matchingCandidate = (item: SkillCandidate) =>
    !needle || `${item.name} ${item.description} ${item.content}`.toLowerCase().includes(needle);
  const shownExperiences = overview.items.filter((item) => {
    if (tab === "skills") return false;
    if (tab === "learned" && item.status !== "active") return false;
    if (tab === "improved" && (item.version <= 1 || item.status !== "active")) return false;
    if (tab === "review" && item.status !== "needs_review") return false;
    if (tab === "rejected" && item.status !== "rejected") return false;
    return matchingExperience(item);
  });
  const shownCandidates = (tab === "review" ? pending : tab === "rejected" ? legacyRejected : [])
    .filter(matchingCandidate);
  const shownSkills = tab === "skills" ? overview.skills.filter((skill) =>
    !needle || skill.name.toLowerCase().includes(needle),
  ) : [];
  const hasResults = shownExperiences.length + shownCandidates.length + shownSkills.length > 0;

  async function act(key: string, operation: () => Promise<unknown>) {
    if (busy) return;
    setBusy(key);
    setError(null);
    try {
      await operation();
      await reload();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(null);
    }
  }

  async function approveCandidate(candidate: SkillCandidate) {
    // The server rejects tool-using or otherwise risky candidates. The
    // human must inspect the full candidate text before confirming.
    if (!window.confirm(
      `Activate "${candidate.name}" without replay evaluation?\n\n` +
      "Only read-only skills are eligible. Review its instructions first; " +
      "all normal tool permissions still apply.",
    )) return;
    await act(candidate.id, () => learningApi.approveCandidate(candidate.id));
  }

  return (
    <section className="learning-workspace" aria-label="Trajecta experience learning">
      <header className="learning-workspace__header">
        <div>
          <div className="learning-workspace__eyebrow"><Sparkles size={15} /> Experience-based learning</div>
          <h3>What Trajecta has learned</h3>
          <p>Preferences, corrections, and successful procedures from real conversations.
            New knowledge is reused when relevant; replay evaluation is not part of everyday chat.</p>
        </div>
        <button type="button" className="learning-workspace__refresh"
          title="Refresh learning records" aria-label="Refresh learning records"
          disabled={loading || busy !== null} onClick={() => setRefetchIndex((x) => x + 1)}>
          <RefreshCw size={16} className={loading ? "settings-spin" : ""} /> Refresh
        </button>
      </header>

      <nav className="learning-workspace__tabs" aria-label="Learning categories">
        {(["learned", "improved", "review", "rejected", "skills"] as View[]).map((view) => (
          <button key={view} type="button" className={view === tab ? "learning-workspace__tab is-active" : "learning-workspace__tab"}
            aria-current={tab === view ? "page" : undefined} onClick={() => setTab(view)}>
            {LABELS[view]} <span>{counts[view]}</span>
          </button>
        ))}
      </nav>

      <label className="learning-workspace__search">
        <Search size={16} />
        <input value={search} onChange={(event) => setSearch(event.target.value)}
          placeholder="Search saved knowledge..." aria-label="Search saved knowledge" />
      </label>

      {error && <div className="settings-error-card" role="alert"><ShieldAlert size={17} />{error}</div>}
      {loading ? (
        <div className="learning-workspace__empty"><RefreshCw size={19} className="settings-spin" />Loading saved knowledge...</div>
      ) : !hasResults ? (
        <div className="learning-workspace__empty">
          {tab === "review" ? <ShieldAlert size={23} /> : tab === "skills" ? <BookOpen size={23} />
            : tab === "rejected" ? <Archive size={23} /> : <Lightbulb size={23} />}
          <strong>Nothing here yet</strong>
          <p>{emptyMessage(tab, Boolean(needle))}</p>
        </div>
      ) : (
        <div className="learning-workspace__records">
          {shownExperiences.map((item) => (
            <article className="learning-workspace__record" key={item.id}>
              <div className="learning-workspace__record-head">
                <div className="learning-workspace__tag"><Lightbulb size={14} />{titleCase(item.kind)}</div>
                <span>v{item.version} · {titleCase(item.status)}</span>
              </div>
              <p className="learning-workspace__content">{item.content}</p>
              <div className="learning-workspace__record-bottom">
                <span><Clock3 size={13} /> {new Date(item.updated_at).toLocaleDateString()}</span>
                {tab === "review" && (
                  <div className="learning-workspace__actions">
                    <button disabled={busy !== null} type="button" onClick={() =>
                      void act(item.id, () => learningApi.review(item.id, "reject"))}>Reject</button>
                    <button className="learning-workspace__primary" disabled={busy !== null} type="button" onClick={() =>
                      void act(item.id, () => learningApi.review(item.id, "approve"))}>
                      <Check size={14} /> Approve
                    </button>
                  </div>
                )}
              </div>
            </article>
          ))}
          {shownCandidates.map((candidate) => (
            <article className="learning-workspace__record" key={`previous-${candidate.id}`}>
              <div className="learning-workspace__record-head">
                <div className="learning-workspace__tag"><Archive size={14} />Previous skill draft</div>
                <span>{titleCase(candidate.status)}</span>
              </div>
              <strong>{candidate.name}</strong>
              <p className="learning-workspace__description">{candidate.description}</p>
              <details className="learning-workspace__details">
                <summary>Read full skill instructions</summary>
                <pre>{candidate.content}</pre>
              </details>
              {tab === "review" && (
                <div className="learning-workspace__actions learning-workspace__actions--end">
                  <button type="button" disabled={busy !== null} onClick={() =>
                    void act(candidate.id, () => settingsApi.rejectSkill(candidate.id, "Rejected by user from learning review"))}>
                    Reject draft
                  </button>
                  {candidate.status === "candidate" && (
                    <button type="button" className="learning-workspace__primary" disabled={busy !== null}
                      onClick={() => void approveCandidate(candidate)}><Check size={14} /> Approve read-only</button>
                  )}
                </div>
              )}
            </article>
          ))}
          {shownSkills.map((skill) => (
            <article className="learning-workspace__record" key={skill.id}>
              <div className="learning-workspace__record-head">
                <div className="learning-workspace__tag"><BookOpen size={14} />{skill.name}</div>
                <span>v{skill.version} · {titleCase(skill.status)}</span>
              </div>
              <div className="learning-workspace__record-bottom">
                <span>A reusable procedural skill</span>
                <SettingsToggle label={`Enable ${skill.name}`} checked={skill.status === "active"}
                  disabled={busy !== null} onChange={(isEnabled) =>
                    void act(skill.id, () => settingsApi.setSkillEnabled(skill.name, isEnabled))} />
              </div>
              <details className="learning-workspace__details" onToggle={(event) => {
                if (event.currentTarget.open) setHistoryFor(skill.name);
                else if (historyFor === skill.name) setHistoryFor(null);
              }}>
                <summary>Version history</summary>
                {historyFor === skill.name && <VersionHistory skillName={skill.name} />}
              </details>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
