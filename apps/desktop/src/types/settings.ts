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