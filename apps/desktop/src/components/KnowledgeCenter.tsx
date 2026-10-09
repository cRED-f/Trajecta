import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity, AlertCircle, BookOpenText, CheckCircle2, ChevronRight, History,
  LibraryBig, ListTodo, RefreshCw, Settings2, ShieldAlert, Sparkles,
  type LucideIcon,
} from "lucide-react";
import { useMemoryCatalog } from "../hooks/use-settings";
import { learningApi } from "../lib/api";
import { learningMemoryApi } from "../lib/learning-memory-api";
import { DashboardMetric, DashboardSection, FriendlyEmpty, LearningFlow } from "./LearningSurface";
import { MemorySettings } from "./settings/MemorySettings";
import { ExperiencePanel } from "./skills/ExperiencePanel";

type Section = "overview" | "memories" | "skills" | "review";
const NAV: { id: Section; label: string; description: string; icon: LucideIcon }[] = [
  { id: "overview", label: "Overview", description: "At a glance", icon: LibraryBig },
  { id: "memories", label: "Memories", description: "What Trajecta knows", icon: BookOpenText },
  { id: "skills", label: "Skills", description: "Reusable abilities", icon: Sparkles },
  { id: "review", label: "Review", description: "Needs your decision", icon: ShieldAlert },
];

/** One user-facing home for facts, experiences, proposed workflows and activated skills. */
export function KnowledgeCenter({ backendOnline, initialSection = "overview" }: {
  backendOnline: boolean; initialSection?: Section;
}) {
  const [section, setSection] = useState<Section>(initialSection);
  const [showRejected, setShowRejected] = useState(false);
  const [diagnosticsOpen, setDiagnosticsOpen] = useState(false);
  const client = useQueryClient();
  const facts = useMemoryCatalog("", backendOnline && section === "overview");
  const learnings = useQuery({
    queryKey: ["learning", "overview"], queryFn: learningApi.overview,
    enabled: backendOnline && section === "overview",
  });
  const procedures = useQuery({
    queryKey: ["memory-center", "procedures", null],
    queryFn: () => learningMemoryApi.procedures(null),
    enabled: backendOnline && section === "overview",
  });
  const activeSkills = learnings.data?.skills.filter((skill) => skill.status === "active").length ?? 0;
  const learnedCount = learnings.data?.items.filter((item) => item.status === "active").length ?? 0;
  const experienceReviews = learnings.data?.items.filter((item) => item.status === "needs_review").length ?? 0;
  // Candidate counts on the dashboard are intentionally scoped to the backend's returned page.
  const candidateReviews = learnings.data?.previous_candidates.filter((item) =>
    item.status !== "archived" && item.status !== "promoted" &&
    !(item.status === "rejected" && typeof item.metadata?.rejection_reason === "string" && item.metadata.rejection_reason.trim())
  ).length ?? 0;
  const workflowReviews = procedures.data?.items.filter((item) => item.status === "needs_review").length ?? 0;
  const reviewCount = experienceReviews + candidateReviews + workflowReviews;
  const overviewFailed = facts.isError || learnings.isError || procedures.isError;
  const overviewLoading = facts.isLoading || learnings.isLoading || procedures.isLoading;

  if (!backendOnline) return <div className="knowledge-hub"><FriendlyEmpty icon={AlertCircle} title="Trajecta is disconnected" description="Reconnect to your backend to open saved memory, learning, or skills." /></div>;
  return <section className="knowledge-hub knowledge-layout" aria-label="Knowledge">
    <div className="knowledge-hub__hero">
      <span className="knowledge-hub__mark"><LibraryBig size={25} strokeWidth={1.65} /></span>
      <div><h1>TRAJECTA KNOWLEDGE</h1>
        <p>Understand what Trajecta remembers, which skills it can use, and what needs your approval.</p></div>
      <button className="knowledge-button knowledge-hub__refresh" type="button" aria-label="Refresh knowledge" onClick={() => void Promise.all([
        client.invalidateQueries({ queryKey: ["memory-center"] }),
        client.invalidateQueries({ queryKey: ["settings", "memory"] }),
        client.invalidateQueries({ queryKey: ["learning", "overview"] }),
      ])}><RefreshCw size={16}/><span>Refresh</span></button>
    </div>

    <nav className="knowledge-hub__tabs" aria-label="Knowledge sections">
      {NAV.map(({ id, label, description, icon: Icon }) => <button key={id} type="button"
        className={`knowledge-hub__tab${section === id ? " is-active" : ""}`}
        aria-current={section === id ? "page" : undefined}
        onClick={() => { setSection(id); setShowRejected(false); }}>
        <span className="knowledge-hub__tab-icon"><Icon size={19} strokeWidth={1.8}/></span>
        <span><strong>{label}</strong><small>{description}</small></span>
        {id === "review" && reviewCount > 0 && section === "overview" && !overviewFailed && <i className="knowledge-navigation__count" aria-label={`${reviewCount} items need review`}>{reviewCount}</i>}
      </button>)}
    </nav>

    {section === "overview" && <div className="knowledge-overview">
      {overviewFailed && <div className="settings-error-card" role="alert"><AlertCircle size={18} /> Some overview sources could not load. Their counts are shown as —. Use Refresh to retry.</div>}
      {overviewLoading && <div className="knowledge-loading"><RefreshCw size={18} className="settings-spin" /> Loading overview…</div>}
      {!overviewLoading && <>
        <div className="knowledge-metrics">
          <DashboardMetric label="Saved facts" value={facts.isError ? "—" : facts.data?.counts.semantic ?? 0} description="Remembered preferences and facts" icon={BookOpenText} onClick={() => setSection("memories")}/>
          <DashboardMetric label="Learnings" value={learnings.isError ? "—" : learnedCount} description="Knowledge from previous interactions" icon={History} onClick={() => setSection("memories")}/>
          <DashboardMetric label="Active skills" value={learnings.isError ? "—" : activeSkills} description="Enabled for future use" icon={Sparkles} onClick={() => setSection("skills")}/>
          <DashboardMetric label="Needs review" value={learnings.isError || procedures.isError ? "—" : reviewCount} description="Suggestions awaiting your decision" icon={ShieldAlert} attention={reviewCount > 0} onClick={() => setSection("review")}/>
        </div>
        <div className="knowledge-overview__columns">
          <DashboardSection title="Attention needed" description="Approvals are always explicit.">
            <div className="knowledge-action-list">
              {workflowReviews > 0 && <button type="button" onClick={() => setSection("review")}><ListTodo size={18} /><span><strong>{workflowReviews} workflow suggestions</strong><small>Check observed steps before accepting.</small></span><ChevronRight size={16}/></button>}
              {experienceReviews + candidateReviews > 0 && <button type="button" onClick={() => setSection("review")}><ShieldAlert size={18}/><span><strong>{experienceReviews + candidateReviews} learning or skill drafts</strong><small>Review knowledge and candidate instructions.</small></span><ChevronRight size={16}/></button>}
              {!reviewCount && !overviewFailed && <div className="knowledge-all-clear"><CheckCircle2 size={22} /><div><strong>All caught up</strong><p>There are no pending reviews among the recently returned records.</p></div></div>}
              {!reviewCount && overviewFailed && <p className="knowledge-overview__note">Pending items may not be shown while sources are unavailable.</p>}
            </div>
          </DashboardSection>
          <LearningFlow variant="skill" />
        </div>
        <p className="knowledge-overview__note">Counters summarize the records returned by the service, not lifetime totals. Review and skill activation are separate actions.</p>
        <details className="knowledge-diagnostics" onToggle={(event) => setDiagnosticsOpen(event.currentTarget.open)}><summary><Settings2 size={17} /> Diagnostics and maintenance <span>Background reviews, memory conflicts and technical controls</span></summary>
          {diagnosticsOpen && <div className="knowledge-diagnostics__body"><MemorySettings enabled backendOnline={backendOnline} view="activity" />
            <MemorySettings enabled backendOnline={backendOnline} view="maintenance" /></div>}
        </details>
      </>}
    </div>}

    {section === "memories" && <MemorySettings enabled backendOnline={backendOnline} />}
    {section === "skills" && <ExperiencePanel enabled={backendOnline} view="skills" compact />}
    {section === "review" && <div className="knowledge-review-stack">
      <div className="knowledge-content__heading"><div><h4>{showRejected ? "Rejected learnings and drafts" : "Review suggestions"}</h4><p>{showRejected ? "Audit rejected knowledge and skill drafts. Workflow statuses remain in Memories → Workflows." : "Nothing is approved or activated without your decision."}</p></div>
        <button type="button" className="knowledge-button" onClick={() => setShowRejected((current) => !current)}>{showRejected ? "Back to pending" : "Rejected history"}</button></div>
      {!showRejected && <DashboardSection title="Workflow suggestions" description="Suggested steps are not executable skills."><MemorySettings enabled backendOnline={backendOnline} view="procedural" reviewOnly /></DashboardSection>}
      <DashboardSection title={showRejected ? "Rejected learnings and drafts" : "Learnings and skill drafts"} description={showRejected ? "Preserved for audit." : "Inspect evidence and approve, reject or activate eligible drafts."}>
        <ExperiencePanel enabled={backendOnline} view={showRejected ? "rejected" : "review"} compact />
      </DashboardSection>
    </div>}
  </section>;
}
