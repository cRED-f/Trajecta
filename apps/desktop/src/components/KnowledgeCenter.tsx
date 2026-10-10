import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, BookOpenText, History, LibraryBig, RefreshCw, Settings2, Sparkles, Workflow, type LucideIcon } from "lucide-react";
import { useMemoryCatalog } from "../hooks/use-settings";
import { learningApi } from "../lib/api";
import { learningMemoryApi } from "../lib/learning-memory-api";
import { DashboardMetric, DashboardSection, FriendlyEmpty, LearningFlow } from "./LearningSurface";
import { MemorySettings } from "./settings/MemorySettings";
import { ExperiencePanel } from "./skills/ExperiencePanel";

type Section = "overview" | "memories" | "skills";
const NAV: { id: Section; label: string; description: string; icon: LucideIcon }[] = [
  { id: "overview", label: "Overview", description: "At a glance", icon: LibraryBig },
  { id: "memories", label: "Memories", description: "What Trajecta knows", icon: BookOpenText },
  { id: "skills", label: "Skills", description: "Reusable abilities", icon: Sparkles },
];

/** Consumer-facing knowledge: background learning is automatic, not an approval inbox. */
export function KnowledgeCenter({ backendOnline, initialSection = "overview" }: {
  backendOnline: boolean; initialSection?: Section;
}) {
  const [section, setSection] = useState<Section>(initialSection);
  const [diagnosticsOpen, setDiagnosticsOpen] = useState(false);
  const client = useQueryClient();
  const overview = backendOnline && section === "overview";
  const facts = useMemoryCatalog("", overview);
  const learnings = useQuery({ queryKey: ["learning", "overview"], queryFn: learningApi.overview, enabled: overview });
  const tasks = useQuery({ queryKey: ["learning", "tasks"], queryFn: learningMemoryApi.tasks, enabled: overview });
  const reflection = useQuery({ queryKey: ["memory-center", "reflection"], queryFn: learningMemoryApi.reflection, enabled: overview });
  const activeSkills = learnings.data?.skills.filter((skill) => skill.status === "active").length ?? 0;
  const learnedCount = learnings.data?.items.filter((item) => item.status === "active").length ?? 0;
  const workingCount = tasks.data?.items.filter((task) => task.status === "active" || task.status === "paused").length ?? 0;
  const failed = facts.isError || learnings.isError || tasks.isError || reflection.isError;
  const loading = facts.isLoading || learnings.isLoading || tasks.isLoading || reflection.isLoading;

  if (!backendOnline) return <div className="knowledge-hub"><FriendlyEmpty icon={AlertCircle} title="Trajecta is disconnected" description="Reconnect to inspect saved memories and skills." /></div>;
  return <section className="knowledge-hub knowledge-layout" aria-label="Knowledge">
    <div className="knowledge-hub__hero">
      <span className="knowledge-hub__mark"><LibraryBig size={25} strokeWidth={1.65} /></span>
      <div><h1>TRAJECTA KNOWLEDGE</h1><p>Trajecta remembers experiences, learns in the background, and evaluates reusable skills automatically.</p></div>
      <button type="button" className="knowledge-button knowledge-hub__refresh" aria-label="Refresh knowledge" onClick={() => void Promise.all([
        client.invalidateQueries({ queryKey: ["memory-center"] }),
        client.invalidateQueries({ queryKey: ["settings", "memory"] }),
        client.invalidateQueries({ queryKey: ["learning"] }),
      ])}><RefreshCw size={16}/><span>Refresh</span></button>
    </div>
    <nav className="knowledge-hub__tabs" aria-label="Knowledge sections">
      {NAV.map(({id,label,description,icon:Icon}) => <button type="button" key={id}
        className={`knowledge-hub__tab${section===id?" is-active":""}`}
        aria-current={section===id?"page":undefined} onClick={() => setSection(id)}>
        <span className="knowledge-hub__tab-icon"><Icon size={19}/></span>
        <span><strong>{label}</strong><small>{description}</small></span>
      </button>)}
    </nav>
    {section === "overview" && <div className="knowledge-overview">
      {failed && <div className="settings-error-card" role="alert"><AlertCircle size={18}/> Some learning data could not load.</div>}
      {loading ? <div className="knowledge-loading"><RefreshCw size={18} className="settings-spin"/> Loading knowledge…</div> : <>
        <div className="knowledge-metrics">
          <DashboardMetric label="Saved facts" value={facts.isError?"—":facts.data?.counts.semantic??0} description="Preferences and facts" icon={BookOpenText} onClick={()=>setSection("memories")}/>
          <DashboardMetric label="Learnings" value={learnings.isError?"—":learnedCount} description="Recorded insights" icon={History} onClick={()=>setSection("memories")}/>
          <DashboardMetric label="Active skills" value={learnings.isError?"—":activeSkills} description="Verified reusable abilities" icon={Sparkles} onClick={()=>setSection("skills")}/>
          <DashboardMetric label="Ongoing tasks" value={tasks.isError?"—":workingCount} description="Task history across sessions" icon={Workflow} onClick={()=>setSection("memories")}/>
        </div>
        <div className="knowledge-overview__columns">
          <DashboardSection title="Automatic learning" description="Your chat does not wait for model-based reflection or skill evaluation.">
            <div className="knowledge-action-list"><div className="knowledge-all-clear"><Workflow size={22}/><div>
              <strong>{reflection.data?.enabled ? "Background learning enabled" : "Background learning disabled"}</strong>
              <p>{reflection.data?.counts?.pending??0} queued · {reflection.data?.counts?.processing??0} processing · {reflection.data?.counts?.completed??0} completed</p>
              <p>Incomplete evaluations remain inactive until reliable evidence is available. Tool permissions are unchanged.</p>
            </div></div></div>
          </DashboardSection>
          <LearningFlow variant="skill" />
        </div>
        <p className="knowledge-overview__note">Task completion is provisional. Skills activate only after the existing evaluation and safety gates pass.</p>
        <details className="knowledge-diagnostics" onToggle={(event)=>setDiagnosticsOpen(event.currentTarget.open)}><summary><Settings2 size={17}/> Diagnostics and maintenance <span>Inspect learning jobs and memory health</span></summary>
          {diagnosticsOpen && <div className="knowledge-diagnostics__body"><MemorySettings enabled backendOnline={backendOnline} view="activity"/><MemorySettings enabled backendOnline={backendOnline} view="maintenance"/></div>}
        </details>
      </>}
    </div>}
    {section==="memories" && <MemorySettings enabled backendOnline={backendOnline}/>}
    {section==="skills" && <ExperiencePanel enabled={backendOnline} view="skills" compact/>}
  </section>;
}
