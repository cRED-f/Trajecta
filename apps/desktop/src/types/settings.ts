export type PermissionMode =
  | "allow"
  | "ask"
  | "deny";

export interface PermissionItem {
  id: string;
  title: string;
  description: string;
  mode: PermissionMode;
}

export interface PermissionCatalog {
  permissions: PermissionItem[];
}

export interface MemoryItem {
  id: string;
  tier: string;
  namespace: string;
  key: string;
  content: string;
  created_at: string;
  updated_at: string;
}

export interface MemoryCatalog {
  settings: {
    automatic_memory: boolean;
  };
  counts: {
    semantic: number;
    procedural: number;
  };
  items: MemoryItem[];
}

export type SkillStatus =
  | "active"
  | "disabled"
  | "verified"
  | "candidate"
  | "evaluating"
  | "rejected"
  | "archived";

export interface SkillRegistryItem {
  id: string;
  name: string;
  version: string;
  status: SkillStatus;
  path: string | null;
  created_at: string;
  metadata: Record<string, unknown>;
}

export interface SkillCandidate {
  id: string;
  name: string;
  description: string;
  content: string;
  status: SkillStatus;
  source_trajectory_ids: string[];
  created_at: string;
  updated_at: string;
  metadata: Record<string, unknown>;
}

export interface SkillCatalog {
  skills: SkillRegistryItem[];
  candidates: SkillCandidate[];
  summary: {
    active: number;
    disabled: number;
    candidate: number;
    evaluating: number;
  };
}

export interface SkillPromotionResult {
  skill_name: string;
  version: string;
  version_id: string;
  previous_version: string | null;
}

export interface SkillVersion {
  version: string;
  status: string;
  created_at: string;
  metadata: Record<string, unknown>;
}

export interface SkillVersionCompareResult {
  skill: string;
  from: { version: string; metadata: Record<string, unknown> };
  to: { version: string; metadata: Record<string, unknown> };
  changed: boolean;
}

export interface SkillRollbackResult {
  skill: string;
  rolled_back_from: string | null;
  rolled_back_to: string;
  reason: string | null;
}

/** POST /skills/upgrade — rejected upgrades carry no skill/version. */
export interface SkillUpgradeResult {
  status: "promoted" | "rejected";
  skill?: string;
  version?: string;
  reason?: string | null;
  evaluation?: string;
}

export interface SkillAggregateMetrics {
  total_cases: number;
  total_runs: number;
  successes: number;
  success_rate: number;
  average_score: number;
  tool_errors: number;
}

export interface SkillEvaluationReport {
  id: string;
  candidate_id: string;
  skill_name: string;
  verdict: string;
  baseline: SkillAggregateMetrics;
  candidate: SkillAggregateMetrics;
  comparison: Record<string, unknown>;
}

export interface SkillLearningRun {
  id: string;
  started_at: string;
  completed_at: string | null;
  status: string;
  checkpoint_before: number;
  observed_success_count: number;
  created_count: number;
  evaluated_count: number;
  verified_count: number;
  promoted_count: number;
  error: string | null;
  metadata: Record<string, unknown>;
}

export interface SkillLearningStatus {
  enabled: boolean;
  worker_running: boolean;
  learning_run_active: boolean;
  total_successful_trajectories: number;
  success_count_checkpoint: number;
  pending_successes: number;
  trigger_every_successes: number;
  minimum_occurrences: number;
  auto_evaluate: boolean;
  auto_promote: boolean;
  recent_runs: SkillLearningRun[];
}

export type ScheduleType =
  | "once"
  | "interval"
  | "cron";

export interface ScheduledTask {
  id: string;
  name: string;
  prompt: string;
  schedule_type: ScheduleType;
  schedule_expr: string;
  timezone: string;
  enabled: boolean;
  created_at: string;
  next_run_at: string | null;
  last_run_at: string | null;
  metadata: Record<string, unknown>;
}

export interface ScheduleCatalog {
  schedules: ScheduledTask[];
  count: number;
}

export interface ScheduleCreateInput {
  name: string;
  prompt: string;
  schedule_type: ScheduleType;
  schedule_expr: string;
  timezone?: string;
  metadata?: Record<string, unknown>;
}

export interface ScheduleUpdateInput {
  name?: string;
  prompt?: string;
  schedule_type?: ScheduleType;
  schedule_expr?: string;
  timezone?: string;
  enabled?: boolean;
}