import { useState } from "react";
import { learningMemoryApi, type Episode } from "../../lib/learning-memory-api";

export function EpisodicMemories({ items, search, busy, workspacePath, perform }: {
  items: Episode[]; search: string; busy: boolean; workspacePath: string | null;
  perform: (action: () => Promise<unknown>) => Promise<void>;
}) {
  const [ratingFor, setRatingFor] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const displayed = items.filter((item) => `${item.goal} ${item.summary} ${item.tool_names.join(" ")}`.toLowerCase().includes(search.toLowerCase()));
  return <div className="memory-center__records">
    <p className="memory-center__note">Episodes are observed history, not proof of task success. User feedback updates the original trajectory.</p>
    {displayed.length === 0 && <div className="settings-empty-state settings-empty-state--small"><strong>No episodes in this scope</strong><span>Substantial completed tasks will appear here after consolidation.</span></div>}
    {displayed.map((episode) => <article className="memory-center__record" key={episode.id}>
      <div className="memory-center__head"><strong>{episode.goal}</strong><span className="memory-center__status">{episode.outcome_verified ? "Outcome verified" : "Unverified outcome"}</span></div>
      <p className="memory-center__note">{episode.created_at ? new Date(episode.created_at).toLocaleString() : ""} · {episode.outcome} · {episode.tool_names.length} tool(s)</p>
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
