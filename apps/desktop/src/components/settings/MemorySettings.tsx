import { useState, type FormEvent } from "react";
import { isTauri } from "@tauri-apps/api/core";
import { open } from "@tauri-apps/plugin-dialog";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity, AlertCircle, BookOpenText, Check, FolderOpen, History,
  Lightbulb, Plus, RefreshCw, Search, ShieldCheck, X,
  type LucideIcon,
} from "lucide-react";
import { useMemoryActions, useMemoryCatalog } from "../../hooks/use-settings";
import { learningMemoryApi } from "../../lib/learning-memory-api";
import { FriendlyEmpty } from "../LearningSurface";
import { EpisodicMemories } from "../memory/EpisodicMemories";
import { ReflectionActivity } from "../memory/ReflectionActivity";
import { MemoryMaintenance } from "../memory/MemoryMaintenance";
import { ExperiencePanel } from "../skills/ExperiencePanel";

type View = "semantic" | "learned" | "episodic" | "activity" | "maintenance";
const SECTIONS: { key: View; label: string; icon: LucideIcon }[] = [
  { key: "semantic", label: "Saved facts", icon: BookOpenText },
  { key: "learned", label: "Learnings", icon: Lightbulb },
  { key: "episodic", label: "Past tasks", icon: History },
];

/** Memory management and optional diagnostics with no routine review inbox. */
export function MemorySettings({ enabled, backendOnline, view }: {
  enabled: boolean; backendOnline: boolean; view?: View;
}) {
  const [localView, setLocalView] = useState<View>("semantic");
  const [search, setSearch] = useState("");
  const [workspacePath, setWorkspacePath] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);
  const [editorOpen, setEditorOpen] = useState(false);
  const [key, setKey] = useState("");
  const [content, setContent] = useState("");
  const tab = view ?? localView;
  const canFetch = enabled && backendOnline;
  const client = useQueryClient();
  const actions = useMemoryActions();
  const semantic = useMemoryCatalog(tab === "semantic" ? search : "", canFetch && tab === "semantic");
  const episodes = useQuery({
    queryKey: ["memory-center", "episodes", workspacePath],
    queryFn: () => learningMemoryApi.episodes(workspacePath),
    enabled: canFetch && tab === "episodic",
  });
  const reflection = useQuery({
    queryKey: ["memory-center", "reflection"],
    queryFn: learningMemoryApi.reflection,
    enabled: canFetch && tab === "activity",
    refetchInterval: canFetch && tab === "activity" ? 8000 : false,
  });
  const findings = useQuery({
    queryKey: ["memory-center", "findings", workspacePath],
    queryFn: () => learningMemoryApi.findings(workspacePath),
    enabled: canFetch && tab === "maintenance",
  });
  const conflicts = useQuery({
    queryKey: ["memory-center", "conflicts"],
    queryFn: learningMemoryApi.conflicts,
    enabled: canFetch && tab === "maintenance" && !workspacePath,
  });
  const currentQuery = tab === "semantic" ? semantic : tab === "episodic" ? episodes
    : tab === "activity" ? reflection : findings;
  const loading = tab !== "learned" && (currentQuery.isLoading || (tab === "maintenance" && !workspacePath && conflicts.isLoading));
  const queryError = tab === "learned" ? null : currentQuery.error ?? (tab === "maintenance" ? conflicts.error : null);

  async function perform(action: () => Promise<unknown>) {
    if (busy) return;
    setBusy(true); setError(null);
    try {
      await action();
      await Promise.all([
        client.invalidateQueries({ queryKey: ["memory-center"] }),
        client.invalidateQueries({ queryKey: ["settings", "memory"] }),
      ]);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Could not save changes."); }
    finally { setBusy(false); }
  }
  async function chooseFolder() {
    try {
      if (!isTauri()) throw new Error("Folder selection requires the desktop app.");
      const picked = await open({ directory: true, multiple: false, title: "Browse knowledge for a workspace" });
      if (typeof picked === "string") setWorkspacePath(picked);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Could not choose folder."); }
  }
  function resetEditor() { setEditing(null); setEditorOpen(false); setKey(""); setContent(""); }
  function editFact(memory: {key: string; content: string}) {
    setEditing(memory.key); setEditorOpen(true); setKey(memory.key); setContent(memory.content);
  }
  async function saveFact(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!key.trim() || !content.trim()) { setError("Both a name and content are required."); return; }
    // Keep the editor open on failure, so a failed save never discards input.
    if (busy) return;
    setBusy(true); setError(null);
    try {
      await actions.addMemory({ key: key.trim(), content: content.trim() });
      await client.invalidateQueries({ queryKey: ["settings", "memory"] });
      resetEditor();
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Could not save fact."); }
    finally { setBusy(false); }
  }

  if (!backendOnline) return <FriendlyEmpty icon={AlertCircle} title="Knowledge is unavailable" description="Reconnect to Trajecta to browse and manage knowledge." />;
  return <section className="knowledge-embedded memory-center" aria-label="Memories">
    {!view && <nav className="knowledge-subnav" aria-label="Types of memory">
      {SECTIONS.map(({ key: section, label, icon: Icon }) => <button key={section} type="button"
        className={tab === section ? "knowledge-subnav__item is-active" : "knowledge-subnav__item"}
        aria-current={tab === section ? "page" : undefined}
        onClick={() => { setLocalView(section); setSearch(""); setError(null); }}>
        <Icon size={16} strokeWidth={1.85} /> {label}
      </button>)}
    </nav>}

    {(tab === "episodic" || tab === "maintenance") && <div className="knowledge-scope">
      <div className="knowledge-scope__description"><FolderOpen size={18} /><div>
        <strong>{workspacePath ? "Workspace" : "All workspaces"}</strong>
        <span title={workspacePath ?? undefined}>{workspacePath ?? "Showing general memory"}</span>
      </div></div><div className="knowledge-scope__actions">
        {workspacePath && <button type="button" className="knowledge-button" onClick={() => setWorkspacePath(null)}>Clear</button>}
        <button type="button" className="knowledge-button" onClick={() => void chooseFolder()}><FolderOpen size={15} /> Choose folder</button>
      </div>
    </div>}

    {tab === "semantic" && <div className="knowledge-content__heading"><div><h4>Saved facts</h4><p>Preferences and information Trajecta can recall in future conversations.</p></div>
      <button type="button" className="knowledge-button knowledge-button--primary" onClick={() => { resetEditor(); setEditorOpen(true); }}><Plus size={16} /> Add fact</button>
    </div>}
    {tab === "semantic" && editorOpen && <form className="knowledge-editor" onSubmit={(event) => void saveFact(event)}>
      <div className="knowledge-content__heading"><strong>{editing ? "Edit fact" : "New fact"}</strong><button type="button" className="knowledge-icon-button" onClick={resetEditor} aria-label="Close editor"><X size={16}/></button></div>
      <label>Name<input value={key} onChange={(event) => setKey(event.target.value)} disabled={busy || Boolean(editing)} maxLength={160} required placeholder="Example: preferred-language" /></label>
      <label>What should Trajecta remember?<textarea value={content} onChange={(event) => setContent(event.target.value)} disabled={busy} rows={3} required /></label>
      <div className="knowledge-editor__actions"><button type="button" className="knowledge-button" onClick={resetEditor}>Cancel</button>
        <button type="submit" className="knowledge-button knowledge-button--primary" disabled={busy || !key.trim() || !content.trim()}>{busy ? <RefreshCw size={15} className="settings-spin"/> : <Check size={15}/>} Save fact</button></div>
    </form>}
    {(tab === "semantic" || tab === "episodic") && <label className="knowledge-search knowledge-search--full"><Search size={17} />
      <input aria-label="Search memories" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search records…" /></label>}
    {error && <div role="alert" className="settings-error-card"><AlertCircle size={17} /> {error}</div>}
    {queryError && <div role="alert" className="settings-error-card"><AlertCircle size={17} /> {queryError instanceof Error ? queryError.message : "Could not load memory."}</div>}
    {loading && <div className="knowledge-loading"><RefreshCw size={17} className="settings-spin" /> Loading records…</div>}
    {!loading && !queryError && tab === "semantic" && <div className="memory-center__records">
      {semantic.data?.items.length === 0 && <FriendlyEmpty icon={BookOpenText} title={search ? "No matching facts" : "No saved facts yet"} description="You can add a fact here or let Trajecta learn from conversations." />}
      {semantic.data?.items.map((memory) => <article className="memory-center__record" key={memory.id}>
        <div className="memory-center__head"><strong>{memory.key}</strong><div className="memory-center__actions">
          <button type="button" disabled={busy} className="memory-center__button" onClick={() => editFact(memory)}>Edit</button>
          <button type="button" disabled={busy} className="memory-center__button" onClick={() => {
            if (window.confirm(`Forget saved fact “${memory.key}”?`)) void perform(() => actions.deleteMemory(memory.key));
          }}>Forget</button></div></div><p>{memory.content}</p>
      </article>)}
    </div>}
    {tab === "learned" && <ExperiencePanel enabled={canFetch} view="learned" compact />}
    {!loading && !queryError && tab === "episodic" && <EpisodicMemories items={episodes.data ?? []} search={search} busy={busy} workspacePath={workspacePath} perform={perform} />}
    {!loading && !queryError && tab === "activity" && <><div className="knowledge-diagnostic-heading"><Activity size={18}/><strong>Background learning</strong></div><ReflectionActivity status={reflection.data} busy={busy} perform={perform} /></>}
    {!loading && !queryError && tab === "maintenance" && <><div className="knowledge-diagnostic-heading"><ShieldCheck size={18}/><strong>Memory health</strong></div><MemoryMaintenance findings={findings.data?.items ?? []} conflicts={workspacePath ? [] : conflicts.data?.items ?? []} workspacePath={workspacePath} busy={busy} perform={perform} /></>}
  </section>;
}
