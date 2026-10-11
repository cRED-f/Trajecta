import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { BookOpen, History, Lightbulb, RefreshCw, Search, Sparkles } from "lucide-react";
import { learningApi, settingsApi, type LearningOverview } from "../../lib/api";
import { FriendlyEmpty, StatusPill } from "../LearningSurface";
import { SettingsToggle } from "../settings/SettingsToggle";
import { VersionHistory } from "./VersionHistory";

type View = "overview" | "learned" | "improved" | "skills";
const EMPTY: LearningOverview = {items:[], skills:[], previous_candidates:[]};

function candidateExplanation(candidate: LearningOverview["previous_candidates"][number]): string {
  if(candidate.status === "evaluating") return "Evaluation in progress. This skill is not active yet.";
  if(candidate.status === "verified") return "Evaluation passed; activation is pending.";
  const meta = candidate.metadata;
  const reason = meta?.evaluation_blocker;
  const verified = typeof meta?.verified_heldout_cases === "number" ? meta.verified_heldout_cases : 0;
  const needed = typeof meta?.required_heldout_cases === "number" ? meta.required_heldout_cases : 2;
  switch(reason) {
    case "awaiting_verified_cases":
      return `${verified}/${needed} independently observed, user-confirmed held-out tasks. More similar verified tasks are needed.`;
    case "sensitive_tools_require_manual_evidence":
      return "Includes tools that cannot be replayed safely; automatic activation is disabled.";
    case "ready_for_evaluation":
      return "Sufficient evidence collected; awaiting background evaluation.";
    case "evaluation_error":
      return `Automatic evaluation failed: ${String(meta?.evaluation_error ?? "unknown error")}`;
    case "evaluation_needs_review":
      return "Evaluation completed without a pass. Review the recorded evaluation before activation.";
    default:
      return "Discovered workflow; Trajecta is collecting independently verifiable cases before evaluation.";
  }
}

export function ExperiencePanel({enabled,view,compact=false}: {enabled:boolean;view?:View;compact?:boolean}) {
  const [localTab,setTab] = useState<View>("overview");
  const [search,setSearch] = useState("");
  const [busy,setBusy] = useState<string|null>(null);
  const [historyFor,setHistoryFor] = useState<string|null>(null);
  const [error,setError] = useState<string|null>(null);
  const client = useQueryClient();
  const tab = view ?? localTab;
  const {data=EMPTY,isLoading,refetch} = useQuery({queryKey:["learning","overview"],queryFn:learningApi.overview,enabled});
  const matching = (value:string) => value.toLowerCase().includes(search.trim().toLowerCase());
  const items = data.items.filter(item => item.status==="active" && (tab==="learned" || (tab==="improved" && item.version>1)) && matching(`${item.content} ${item.kind}`));
  const saved = data.skills.filter(item => tab==="skills" && matching(item.name));
  const pending = data.previous_candidates.filter(item => ["candidate","evaluating","verified"].includes(item.status) && matching(`${item.name} ${item.description}`));
  const blocked = pending.filter(item => item.status==="candidate");

  async function toggleSkill(name:string,enabled:boolean) {
    if(busy) return;
    setBusy(name);setError(null);
    try {await settingsApi.setSkillEnabled(name,enabled);await client.invalidateQueries({queryKey:["learning","overview"]});}
    catch(cause) {setError(cause instanceof Error?cause.message:"Could not update skill");}
    finally {setBusy(null);}
  }
  return <section className={compact?"knowledge-embedded learning-workspace":"knowledge-layout learning-workspace"} aria-label="Knowledge and skills">
    {!compact && <header className="knowledge-header"><div><div className="knowledge-header__eyebrow"><Sparkles size={16}/> AUTONOMOUS LEARNING</div>
      <h3>Knowledge & skills</h3><p>Trajecta records experiences and evaluates new abilities automatically. Tool permissions still apply.</p></div>
      <button type="button" className="knowledge-button" disabled={isLoading} onClick={()=>void refetch()}><RefreshCw size={16}/> Refresh</button>
    </header>}
    {!view && <nav className="knowledge-navigation" aria-label="Learning sections">
      {([ ["overview","Overview",Sparkles],["learned","Learnings",Lightbulb],["improved","Improvements",History],["skills","Skills",BookOpen] ] as const).map(([id,label,Icon]) => <button type="button" key={id} onClick={()=>setTab(id)} className={tab===id?"knowledge-navigation__item is-active":"knowledge-navigation__item"}>
        <Icon size={18}/><strong>{label}</strong>
      </button>)}
    </nav>}
    {error && <p role="alert" className="memory-center__warning">{error}</p>}
    <label className="knowledge-search"><Search size={17}/><input value={search} onChange={e=>setSearch(e.target.value)} placeholder="Search skills or learnings"/></label>
    {isLoading && <p className="memory-center__note">Loading learning activity…</p>}
    {tab==="overview" && !isLoading && <div className="learning-workspace__records">
      <article className="learning-workspace__record"><h5>Saved insights: {data.items.filter(x=>x.status==="active").length}</h5>
        <p>Facts, corrections, and recurring observations can inform later conversations.</p></article>
      <article className="learning-workspace__record"><h5>Active skills: {data.skills.filter(x=>x.status==="active").length}</h5>
        <p>Only evaluated improvements are activated. {pending.length} in the candidate pipeline; {blocked.length} still collecting evidence or awaiting review.</p></article>
    </div>}
    {(tab==="learned"||tab==="improved") && !isLoading && <div className="learning-workspace__records">
      {items.length===0 && <FriendlyEmpty icon={Lightbulb} title="No matching learnings" description="Trajecta will consolidate useful interactions automatically."/>}
      {items.map(item=><article className="learning-workspace__record" key={item.id}>
        <div className="learning-workspace__record-head"><span className="learning-workspace__tag">{item.kind}</span><StatusPill status={item.status}/></div>
        <p className="learning-workspace__description">{item.content}</p><small>Version {item.version}</small>
      </article>)}
    </div>}
    {tab==="skills" && !isLoading && <div className="learning-workspace__records">
      {data.skill_evidence?.workspace_replay_enabled===false && <article className="learning-workspace__record">
        <h5>Automatic skill evaluation needs replay evidence</h5>
        <p className="learning-workspace__description">Workspace replay capture is disabled. New candidate workflows can still be saved, but file-changing skills cannot automatically collect verifiable test cases. To opt in, enable TRAJECTA_SKILLS__FIXTURES__ENABLED=true and TRAJECTA_SKILLS__FIXTURES__CAPTURE_WORKSPACE=true before starting the backend. This captures bounded local workspace snapshots and can add disk and startup-task overhead.</p>
      </article>}
      {saved.length===0 && <FriendlyEmpty icon={BookOpen} title="No registered skills yet" description="Discovered procedures appear below. Skills only become active after an independently graded replay passes."/>}
      {saved.map(skill=><article className="learning-workspace__record" key={skill.id}>
        <div className="learning-workspace__record-head"><span className="learning-workspace__tag"><BookOpen size={15}/> Reusable skill</span><StatusPill status={skill.status}/></div>
        <h5 className="knowledge-record-title">{skill.name}</h5><p className="learning-workspace__description">Version {skill.version}</p>
        <div className="learning-workspace__record-bottom"><span>Enable for future use</span><SettingsToggle label={`Enable ${skill.name}`} checked={skill.status==="active"} disabled={busy!==null} onChange={on=>void toggleSkill(skill.name,on)}/></div>
        <details onToggle={e=>setHistoryFor(e.currentTarget.open?skill.name:null)}><summary>Version history</summary>{historyFor===skill.name && <VersionHistory skillName={skill.name}/>}</details>
      </article>)}
      {pending.length>0 && <div className="learning-workspace__records" aria-label="Skill candidates">
        <h4>Discovered workflows ({pending.length})</h4>
        <p className="memory-center__note">Candidates are saved automatically, but cannot use tools until verified and activated. Unsafe actions still need their usual permissions.</p>
        {pending.map(candidate=><article className="learning-workspace__record" key={candidate.id}>
          <div className="learning-workspace__record-head"><span className="learning-workspace__tag">Skill candidate</span><StatusPill status={candidate.status}/></div>
          <h5 className="knowledge-record-title">{candidate.name}</h5>
          <p className="learning-workspace__description">{candidate.description}</p>
          <p className="memory-center__note">{candidateExplanation(candidate)}</p>
        </article>)}
      </div>}
      {pending.length===0 && <p className="memory-center__note">No pending skill candidates. Background reviews only create a candidate from an evidence-linked procedural insight and successful observed tool steps.</p>}
    </div>}
  </section>;
}
