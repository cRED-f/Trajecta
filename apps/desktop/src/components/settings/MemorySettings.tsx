import { useState } from "react";
import { isTauri } from "@tauri-apps/api/core";
import { open } from "@tauri-apps/plugin-dialog";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, Brain, FolderOpen, RefreshCw } from "lucide-react";
import { useMemoryActions, useMemoryCatalog } from "../../hooks/use-settings";
import { settingsApi } from "../../lib/api";
import { learningMemoryApi } from "../../lib/learning-memory-api";
import { EpisodicMemories } from "../memory/EpisodicMemories";
import { ProceduralMemories } from "../memory/ProceduralMemories";
import { ReflectionActivity } from "../memory/ReflectionActivity";
import { MemoryMaintenance } from "../memory/MemoryMaintenance";

type Tab = "semantic" | "episodic" | "procedural" | "activity" | "maintenance";
const tabs: { key: Tab; label: string }[] = [
  { key: "semantic", label: "Semantic" },
  { key: "episodic", label: "Episodic" },
  { key: "procedural", label: "Procedural" },
  { key: "activity", label: "Learning activity" },
  { key: "maintenance", label: "Maintenance" },
];

export function MemorySettings({ enabled, backendOnline }: { enabled: boolean; backendOnline: boolean }) {
  const [tab, setTab] = useState<Tab>("semantic");
  const [search, setSearch] = useState("");
  const [workspacePath, setWorkspacePath] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const canFetch = enabled && backendOnline;
  const client = useQueryClient();
  const semantic = useMemoryCatalog(search, canFetch && tab === "semantic");
  const semanticActions = useMemoryActions();
  const episodes = useQuery({
    queryKey: ["memory-center", "episodes", workspacePath],
    queryFn: () => learningMemoryApi.episodes(workspacePath),
    enabled: canFetch && tab === "episodic",
  });
  const procedures = useQuery({
    queryKey: ["memory-center", "procedures", workspacePath],
    queryFn: () => learningMemoryApi.procedures(workspacePath),
    enabled: canFetch && tab === "procedural",
  });
  const skillCatalog = useQuery({
    queryKey: ["memory-center", "active-skills"],
    queryFn: settingsApi.skills,
    enabled: canFetch && tab === "procedural" && !workspacePath,
  });
  const archived = useQuery({
    queryKey: ["memory-center", "archived", workspacePath],
    queryFn: () => learningMemoryApi.procedures(workspacePath, "archived"),
    enabled: canFetch && tab === "procedural",
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
  // The current conflict API is global; do not display it on a workspace-specific view.
  const conflicts = useQuery({
    queryKey: ["memory-center", "conflicts"],
    queryFn: learningMemoryApi.conflicts,
    enabled: canFetch && tab === "maintenance" && !workspacePath,
  });

  async function perform(action: () => Promise<unknown>) {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await action();
      await Promise.all([
        client.invalidateQueries({ queryKey: ["memory-center"] }),
        client.invalidateQueries({ queryKey: ["settings", "memory"] }),
      ]);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Request failed");
    } finally {
      setBusy(false);
    }
  }
  async function selectFolder() {
    try {
      if (!isTauri()) throw new Error("Folder selection requires the desktop app.");
      const path = await open({ directory: true, multiple: false, title: "Browse memory for a workspace" });
      if (typeof path === "string") setWorkspacePath(path);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not select folder");
    }
  }
  const loading = {
    semantic: semantic.isLoading, episodic: episodes.isLoading,
    procedural: procedures.isLoading || archived.isLoading,
    activity: reflection.isLoading, maintenance: findings.isLoading || (!workspacePath && conflicts.isLoading),
  }[tab];
  const queryError = {
    semantic: semantic.error, episodic: episodes.error, procedural: procedures.error ?? archived.error,
    activity: reflection.error, maintenance: findings.error ?? (!workspacePath ? conflicts.error : null),
  }[tab];

  return (
    <section className="memory-center">
      {!backendOnline ? <div className="settings-empty-state"><AlertCircle size={22} /><strong>Backend disconnected</strong><span>Connect to Trajecta to browse saved knowledge.</span></div> : <>
        <div className="memory-center__intro">
          <div><h3>Memory & learning</h3><p>Review facts, previous task experiences, and procedures. Saved knowledge is not automatically a verified skill.</p></div>
          <button type="button" className="memory-center__button" disabled={loading} onClick={() => void Promise.all([
            client.invalidateQueries({ queryKey: ["memory-center"] }),
            client.invalidateQueries({ queryKey: ["settings", "memory"] }),
          ])}><RefreshCw size={15} /> Refresh</button>
        </div>
        <nav className="memory-center__tabs" aria-label="Memory categories">
          {tabs.map((item) => <button type="button" key={item.key} aria-current={tab === item.key ? "page" : undefined}
            className={tab === item.key ? "is-active" : ""} onClick={() => { setTab(item.key); setSearch(""); setError(null); }}>{item.label}</button>)}
        </nav>
        {tab !== "semantic" && tab !== "activity" && <div className="memory-center__scope">
          <label>Memory scope</label>
          <strong title={workspacePath ?? "General (no workspace)"}>{workspacePath ?? "General memory"}</strong>
          <button type="button" className="memory-center__button" onClick={() => void selectFolder()}><FolderOpen size={14} /> Choose folder</button>
          {workspacePath && <button type="button" className="memory-center__button" onClick={() => setWorkspacePath(null)}>General memory</button>}
        </div>}
        {(tab === "semantic" || tab === "episodic" || tab === "procedural") && <label className="memory-center__search">
          <span>Search {tab} memory</span>
          <input aria-label={`Search ${tab} memory`} value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Filter saved records…" />
        </label>}
        {error && <div role="alert" className="settings-error-card"><AlertCircle size={17} /> {error}</div>}
        {queryError && <div role="alert" className="settings-error-card"><AlertCircle size={17} /> {queryError instanceof Error ? queryError.message : "Could not load memory"}</div>}
        {loading ? <div className="settings-loading"><RefreshCw className="settings-spin" size={17} /> Loading…</div> : null}
        {!loading && !queryError && tab === "semantic" && <div className="memory-center__records">
          <p className="memory-center__note">Saved preferences and facts. {semantic.data?.counts.semantic ?? 0} matching entries.</p>
          {semantic.data?.items.length === 0 && <div className="settings-empty-state settings-empty-state--small"><Brain size={20} /><strong>No semantic memories</strong><span>Facts saved by the agent will appear here.</span></div>}
          {semantic.data?.items.map((memory) => <article className="memory-center__record" key={memory.id}>
            <div className="memory-center__head"><strong>{memory.key}</strong><button disabled={busy || semanticActions.deletingMemory} type="button" className="memory-center__button" onClick={() => {
              if (window.confirm(`Forget semantic memory “${memory.key}”?`)) void perform(() => semanticActions.deleteMemory(memory.key));
            }}>Forget</button></div><p>{memory.content}</p></article>)}
        </div>}
        {!loading && !queryError && tab === "episodic" && <EpisodicMemories items={episodes.data ?? []} search={search} busy={busy} workspacePath={workspacePath} perform={perform} />}
        {!loading && !queryError && tab === "procedural" && <ProceduralMemories items={procedures.data?.items ?? []} archived={archived.data?.items ?? []} activeSkills={workspacePath ? [] : skillCatalog.data?.skills.filter((s) => s.status === "active") ?? []} search={search} busy={busy} workspacePath={workspacePath} perform={perform} />}
        {!loading && !queryError && tab === "activity" && <ReflectionActivity status={reflection.data} busy={busy} perform={perform} />}
        {!loading && !queryError && tab === "maintenance" && <MemoryMaintenance findings={findings.data?.items ?? []} conflicts={workspacePath ? [] : conflicts.data?.items ?? []} workspacePath={workspacePath} busy={busy} perform={perform} />}
      </>}
    </section>
  );
}
