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

export interface LogicalTask {
  id: string; goal: string; status: string; thread_id: string;
  created_at: string; updated_at: string; scope: string;
}
export interface ReflectionJob {
  id: string;
  task_id: string;
  reason: string;
  status: string;
  attempts: number;
  last_error: string | null;
  updated_at: string;
  result: null | {
    summary?: string;
    model?: string;
    scope?: string;
    skill_candidates?: string[];
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
  tasks: () => request<{ items: LogicalTask[] }>("/learning/tasks"),
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
