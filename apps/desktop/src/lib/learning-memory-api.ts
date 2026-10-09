/** Typed client contracts for the persisted experience-first memory APIs. */
import { request } from "./api";

export interface Episode {
  id: string;
  source_trajectory_id: string;
  scope: string;
  goal: string;
  summary: string;
  outcome: string;
  outcome_verified: boolean;
  tool_names: string[];
  evidence: { source_event_seqs?: number[]; verification?: string };
  created_at: string;
}

export interface ProcedureStep {
  tool: string;
  event_seq: number;
  result_seq: number | null;
  outcome: string;
}
export interface ProcedureDraft {
  id: string;
  title: string;
  rationale: string;
  scope: string;
  kind: "revision" | "new";
  target_skill_name: string | null;
  base_skill_version: string | null;
  status: "needs_review" | "approved" | "rejected" | "candidate_created";
  version: number;
  source_trajectory_id: string;
  candidate_id: string | null;
  user_confirmed: boolean;
  archived_at: string | null;
  steps: ProcedureStep[];
  evidence: { source_event_seqs?: number[]; feedback?: string; risk_review_required?: boolean };
  updated_at: string;
}
export interface ProcedureRevision {
  version: number;
  snapshot: ProcedureDraft;
  created_at: string;
}
export interface ReflectionJob {
  id: string;
  trajectory_id: string;
  reason: string;
  status: string;
  attempts: number;
  last_error: string | null;
  updated_at: string;
  result: null | {
    summary?: string;
    model?: string;
    scope?: string;
    procedure_draft_id?: string;
    insights?: { kind: string; content: string; confidence: number; evidence_event_seqs: number[] }[];
  };
}
export interface ReflectionStatus {
  enabled: boolean;
  counts: Record<string, number>;
  jobs: ReflectionJob[];
}
export interface ReflectionSettings {
  enabled: boolean;
  model: string | null;
  max_daily_reviews: number;
  max_output_tokens: number;
  timeout_seconds: number;
}
export interface MemoryConflict {
  id: number;
  left_ref: string;
  right_ref: string;
  status: string;
  preferred_ref: string | null;
  created_at: string;
}
export interface CuratorFinding {
  id: number;
  item_id: string;
  item_type: string;
  finding_type: string;
  summary: string;
  status: string;
  created_at: string;
}

const params = (pairs: Record<string, string | number | undefined | null>) => {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(pairs)) {
    if (value !== undefined && value !== null && value !== "") search.set(key, String(value));
  }
  const output = search.toString();
  return output ? `?${output}` : "";
};
const post = <T>(path: string, body?: object) =>
  request<T>(path, { method: "POST", ...(body ? { body: JSON.stringify(body) } : {}) });

export const learningMemoryApi = {
  episodes: (workspacePath: string | null) => request<Episode[]>(`/memory/episodic${params({ workspace_path: workspacePath, limit: 100 })}`),
  deleteEpisode: (id: string, workspacePath: string | null) =>
    request<{ ok: boolean }>(`/memory/episodic/${encodeURIComponent(id)}${params({ workspace_path: workspacePath })}`, { method: "DELETE" }),
  procedures: (workspacePath: string | null, status?: string) =>
    request<{ items: ProcedureDraft[] }>(`/learning/procedures${params({ workspace_path: workspacePath, status, limit: 100 })}`),
  history: (id: string, workspacePath: string | null) =>
    request<{ items: ProcedureRevision[] }>(`/learning/procedures/${encodeURIComponent(id)}/history${params({ workspace_path: workspacePath })}`),
  review: (id: string, decision: "approve" | "reject", workspacePath: string | null) =>
    post<ProcedureDraft>(`/learning/procedures/${encodeURIComponent(id)}/review${params({ workspace_path: workspacePath })}`, { decision }),
  candidate: (id: string, name: string | null, workspacePath: string | null) =>
    post<{ id: string }>(`/learning/procedures/${encodeURIComponent(id)}/candidate${params({ workspace_path: workspacePath })}`, { name }),
  archive: (id: string, workspacePath: string | null) =>
    post<{ ok: boolean }>(`/memory/curator/procedures/${encodeURIComponent(id)}/archive${params({ workspace_path: workspacePath })}`),
  restore: (id: string, workspacePath: string | null) =>
    post<{ ok: boolean }>(`/memory/curator/procedures/${encodeURIComponent(id)}/restore${params({ workspace_path: workspacePath })}`),
  reflection: () => request<ReflectionStatus>("/learning/reflection/status"),
  reflectionSettings: () => request<ReflectionSettings>("/learning/reflection/settings"),
  setReflectionSettings: (patch: Partial<ReflectionSettings>) =>
    request<ReflectionSettings>("/learning/reflection/settings", { method: "PATCH", body: JSON.stringify(patch) }),
  conflicts: () => request<{ items: MemoryConflict[] }>("/memory/unified/conflicts"),
  resolve: (id: number, preferredRef: string) =>
    post<{ ok: boolean }>(`/memory/unified/conflicts/${id}/resolve`, { preferred_ref: preferredRef }),
  findings: (workspacePath: string | null) =>
    request<{ items: CuratorFinding[] }>(`/memory/curator/findings${params({ workspace_path: workspacePath })}`),
  scan: (workspacePath: string | null) =>
    post<{ items: CuratorFinding[] }>(`/memory/curator/scan${params({ workspace_path: workspacePath })}`),
  dismiss: (id: number, workspacePath: string | null) =>
    post<{ ok: boolean }>(`/memory/curator/findings/${id}/dismiss${params({ workspace_path: workspacePath })}`),
  feedback: (id: string, rating: "success" | "failure", note: string) =>
    post<unknown>("/learning/feedback", { trajectory_id: id, rating, note }),
};
