import { useState } from "react";
import { Clock3, History } from "lucide-react";
import { FriendlyEmpty, StatusPill } from "../LearningSurface";
import { learningMemoryApi, type Episode } from "../../lib/learning-memory-api";

export function EpisodicMemories({ items, search, busy, workspacePath, perform }: {
  items: Episode[]; search: string; busy: boolean; workspacePath: string | null;
  perform: (action: () => Promise<unknown>) => Promise<void>;
}) {
  const [ratingFor, setRatingFor] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const displayed = items.filter((item) => `${item.goal} ${item.summary} ${item.tool_names.join(" ")}`.toLowerCase().includes(search.toLowerCase()));
  return <div className="memory-center__records">
    <p className="memory-center__note">A task record describes what happened, not necessarily whether it succeeded. Give feedback to improve future learning.</p>
    {displayed.length === 0 && <FriendlyEmpty icon={History} title={search ? "No matching past tasks" : "No past tasks in this scope"} description={search ? "Try another search term." : "Substantial completed tasks will appear here after consolidation."} />}
    {displayed.map((episode) => <article className="memory-center__record" key={episode.id}>
      <div className="memory-center__head"><div className="knowledge-record-heading"><History size={18} /><strong>{episode.goal}</strong></div><StatusPill status={episode.outcome_verified ? "active" : "neutral"}>{episode.outcome_verified ? "Outcome verified" : "Not verified"}</StatusPill></div>
      <div className="knowledge-record-subtitle"><span><Clock3 size={14} />{episode.created_at ? new Date(episode.created_at).toLocaleString() : "Date unavailable"}</span><span>{episode.outcome}</span><span>{episode.tool_names.length} tools used</span></div>
      {episode.tool_names.length > 0 && <div className="knowledge-record-tags">{episode.tool_names.slice(0, 4).map((tool) => <span key={tool}>{tool}</span>)}{episode.tool_names.length > 4 && <span>+{episode.tool_names.length - 4} more</span>}</div>}
      <details><summary>Read experience and evidence</summary><p className="memory-center__preserve">{episode.summary}</p>
        <p className="memory-center__note">Trajectory: <code>{episode.source_trajectory_id}</code></p>
        <p className="memory-center__note">Evidence events: {episode.evidence.source_event_seqs?.join(", ") || "None recorded"}</p>
      </details>
      <div className="memory-center__actions">
        <button type="button" disabled={busy} className="memory-center__button" onClick={() => { setRatingFor(ratingFor === episode.id ? null : episode.id); setNote(""); }}>Give feedback</button>
        <button type="button" disabled={busy} className="memory-center__button" onClick={() => {
          if (window.confirm("Delete this episodic record and its retrieval index?")) void perform(() => learningMemoryApi.deleteEpisode(episode.id, workspacePath));
        }}>Delete episode</button>
      </div>
      {ratingFor === episode.id && <div className="memory-center__feedback">
        <label>Optional feedback note<textarea value={note} maxLength={1000} onChange={(event) => setNote(event.target.value)} rows={2} /></label>
        <div className="memory-center__actions">
          {(["success", "failure"] as const).map((rating) => <button key={rating} type="button" disabled={busy} className="memory-center__button" onClick={() => void perform(async () => {
            await learningMemoryApi.feedback(episode.source_trajectory_id, rating, note);
            setRatingFor(null); setNote("");
          })}>{rating === "success" ? "Mark successful" : "Report failure"}</button>)}
        </div>
      </div>}
    </article>)}
  </div>;
}
