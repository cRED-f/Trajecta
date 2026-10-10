import { useState } from "react";
import { learningMemoryApi, type CuratorFinding, type MemoryConflict } from "../../lib/learning-memory-api";

export function MemoryMaintenance({ findings, conflicts, workspacePath, busy, perform }: {
  findings: CuratorFinding[]; conflicts: MemoryConflict[]; workspacePath: string | null; busy: boolean;
  perform: (action: () => Promise<unknown>) => Promise<void>;
}) {
  const [preferred, setPreferred] = useState<Record<number, string>>({});
  return <div className="memory-center__records">
    <div className="memory-center__head"><div><h4>Memory curator</h4><p className="memory-center__note">Scans only flag items for review; they do not silently delete or activate anything.</p></div>
      <button type="button" className="memory-center__button memory-center__button--primary" disabled={busy} onClick={() => void perform(() => learningMemoryApi.scan(workspacePath))}>Scan for issues</button></div>
    {findings.filter((f) => f.status === "open").length === 0 && <p className="memory-center__note">No open findings for this scope.</p>}
    {findings.filter((f) => f.status === "open").map((finding) => <article className="memory-center__record" key={finding.id}>
      <div className="memory-center__head"><strong>{finding.finding_type.replaceAll("_", " ")}</strong><span className="memory-center__status">{finding.item_type}</span></div>
      <p>{finding.summary}</p><p className="memory-center__note">Affected record: {finding.item_id}</p>
      <button type="button" disabled={busy} className="memory-center__button" onClick={() => void perform(() => learningMemoryApi.dismiss(finding.id, workspacePath))}>Dismiss finding</button>
    </article>)}
    <h4>Conflicting knowledge</h4>
    {workspacePath ? <p className="memory-center__note">Conflict inspection is currently available only for general memory. Return to general scope to resolve conflicts.</p>
      : conflicts.length === 0 ? <p className="memory-center__note">No unresolved general-memory conflicts.</p> : conflicts.map((conflict) => <article className="memory-center__record" key={conflict.id}>
        <p className="memory-center__note">Select the preferred evidence reference; conflicting records remain available for audit.</p>
        <label className="memory-center__choice"><input type="radio" name={`preferred-${conflict.id}`} checked={(preferred[conflict.id] ?? "") === conflict.left_ref} onChange={() => setPreferred((state) => ({ ...state, [conflict.id]: conflict.left_ref }))} />{conflict.left_ref}</label>
        <label className="memory-center__choice"><input type="radio" name={`preferred-${conflict.id}`} checked={(preferred[conflict.id] ?? "") === conflict.right_ref} onChange={() => setPreferred((state) => ({ ...state, [conflict.id]: conflict.right_ref }))} />{conflict.right_ref}</label>
        <button type="button" className="memory-center__button memory-center__button--primary" disabled={busy || !preferred[conflict.id]} onClick={() => {
          const preferredRef = preferred[conflict.id];
          if (!preferredRef) return;
          void perform(() => learningMemoryApi.resolve(conflict.id, preferredRef));
        }}>Resolve conflict</button>
      </article>)}
  </div>;
}
