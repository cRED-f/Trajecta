export type GuardrailPromptAction = "warn" | "block";

export type GuardrailSensitiveAction = "allow" | "redact" | "block";

export interface ContentGuardrailSettings {
  enabled: boolean;

  prompt_injection_enabled: boolean;
  prompt_injection_heuristic_threshold: number;

  user_prompt_action: GuardrailPromptAction;
  untrusted_content_action: GuardrailPromptAction;

  jailbreak_enabled: boolean;
  jailbreak_threshold: number;

  secrets_enabled: boolean;

  pii_enabled: boolean;
  pii_entities: string[];

  local_providers: string[];

  cloud_sensitive_action: GuardrailSensitiveAction;
}

export interface ContentGuardrailCatalog {
  content: ContentGuardrailSettings;
}

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

export interface OllamaEmbeddingModel {
  name: string;
  size: number;
  modified_at: string | null;
  parameter_size: string | null;
  quantization_level: string | null;
  family: string | null;
  capabilities: string[];
}

/** GET /memory/embedding — active embedder plus installed Ollama models. */
export interface EmbeddingCatalog {
  vector_store_enabled: boolean;
  /** Switch position: off means the built-in default embedder is in use. */
  enabled: boolean;
  provider: "placeholder" | "ollama" | string;
  /** Live model while running, otherwise the remembered one. */
  selected_model: string | null;
  dimensions: number;
  ollama: {
    base_url: string;
    reachable: boolean;
    error: string | null;
  };
  models: OllamaEmbeddingModel[];
}

/** PATCH /memory/embedding — switched state/model and what had to be re-indexed. */
export interface EmbeddingSelectionResult {
  enabled: boolean;
  provider: "ollama" | "placeholder";
  selected_model: string | null;
  dimensions: number;
  reindexed: {
    memories: number;
    attachment_chunks: number;
  };
}

export type SkillStatus =
  | "active"
  | "disabled"
  | "verified"
  | "experimenting"
  | "promoted"
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
  experiments: SkillExperiment[];
  summary: {
    active: number;
    disabled: number;
    candidate: number;
    evaluating: number;
    experimenting: number;
    running_experiments: number;
  };
}

export interface SkillPromotionResult {
  skill_name: string;
  version: string;
  version_id: string;
  previous_version: string | null;
}

export interface SkillVersion {
  id: string;
  skill_name: string;

  version: string;
  status: string;

  content_hash: string;

  source_candidate_id: string | null;
  source_evaluation_id: string | null;

  created_at: string;

  metadata: Record<string, unknown>;
}

export interface SkillVersionCompareResult {
  skill: string;
  from: { version: string; metadata: Record<string, unknown> };
  to: { version: string; metadata: Record<string, unknown> };
  changed: boolean;
}

/** POST /skills/upgrade — rejected upgrades carry no skill/version. */
export interface SkillUpgradeResult {
  status: "promoted" | "rejected";
  skill?: string;
  version?: string;
  reason?: string | null;
  evaluation?: string;

  verdict?: string;
  reasons?: string[];
  report?: SkillEvaluationReport;
}

export interface SkillAggregateMetrics {
  total_cases: number;
  total_runs: number;
  graded_cases: number;
  graded_runs: number;
  skipped_runs: number;
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
  comparison: Record<string, unknown> & {
    reasons?: string[];
  };
  case_results?: Array<Record<string, unknown>>;
}

export interface SkillMetricSummary {
  skill_name: string;
  version: string;

  total: number;

  successes: number;
  failures: number;

  success_rate: number;

  average_duration_seconds: number;

  average_input_tokens: number;
  average_output_tokens: number;
  average_total_tokens: number;

  average_tool_calls: number;
  average_tool_errors: number;

  tool_error_rate: number;
}

/** One version of a running A/B / bandit split. */
export interface SkillExperimentArm {
  experiment_id: string;

  version: string;

  candidate_id: string | null;

  is_control: number;

  created_at: string;

  metadata: Record<string, unknown>;

  summary: SkillMetricSummary;
}

/** One row of ``skill_experiments`` with its arms and live metrics. */
export interface SkillExperiment {
  id: string;

  skill_name: string;

  control_version: string;

  strategy: "ab" | "thompson";

  status: string;

  traffic_percent: number;

  min_samples_per_arm: number;
  max_samples_total: number;

  alpha: number;

  bayesian_threshold: number;

  minimum_effect: number;
  harm_effect: number;

  auto_stop: boolean;
  auto_promote: boolean;

  winner_version: string | null;

  reason: string | null;

  created_at: string;
  completed_at: string | null;

  metadata: Record<string, unknown>;

  arms: SkillExperimentArm[];
}

/** One row of the automatic rollback log (``skill_regressions``). */
export interface SkillRegression {
  id: string;

  skill_name: string;

  bad_version: string;
  stable_version: string;

  severity: string;

  reasons: string[];

  evidence: Record<string, unknown>;

  rolled_back: boolean;

  rollback_error: string | null;

  created_at: string;
}

/** Declared edge of the skill dependency graph. */
export interface SkillDependency {
  skill_name: string;

  depends_on_skill: string;

  version_constraint: string;

  required: boolean;

  created_at: string;

  metadata: Record<string, unknown>;
}

/**
 * GET /skills/{name}/analytics — per-version metric summaries, the
 * experiments opened for this skill and its regression history.
 * Metric fields are always numbers: the API normalizes SQLite's
 * NULL averages to 0.
 */
export interface SkillAnalytics {
  skill_name: string;

  versions: Array<{
    version: string;

    status: string;

    created_at: string;

    metadata: Record<string, unknown>;

    metrics: SkillMetricSummary;
  }>;

  experiments: SkillExperiment[];

  regressions: SkillRegression[];
}

export type LlmProviderType =
  | "openai"
  | "anthropic"
  | "ollama"
  | "openai_compat";

/** GET /llm — Bifrost gateway status plus defaults and provider entries. */
export interface LlmCatalog {
  gateway: {
    type: string;
    url: string | null;
    reachable: boolean;
  };
  default_provider: string;
  default_model: string;
  providers: LlmProviderEntry[];
}

export interface LlmProviderEntry {
  id: string;
  type: LlmProviderType;
  configured: boolean;
  reachable: boolean;
}

/** PUT /llm/providers/{id} — keys go to Bifrost, never agent_settings. */
export interface LlmProviderUpsert {
  type: LlmProviderType;
  base_url?: string | null;
  api_key?: string | null;
  extra_headers?: Record<string, string> | null;
}

/** POST /llm/providers/{id}/test */
export interface LlmTestResult {
  reachable: boolean;
  status: string;
  detail?: string | null;
}

/** PUT /llm/default — applied to NEW conversations only. */
export interface LlmDefaultUpdate {
  default_provider: string;
  default_model: string;
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