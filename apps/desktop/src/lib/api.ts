import { consumeSSE } from "./sse";
import type { SkillEvalEvent } from "../components/skills/SkillEvaluationWorkbench";

import type {
  ApprovalDecision,
  Attachment,
  ChatBranch,
  ChatStreamEvent,
  Conversation,
  ConversationDetail,
  HealthResponse,
  ModelCatalog,
  PendingApproval,
  SavedMemory,
} from "../types/chat";

import type { McpCatalog } from "../types/tools";

import type {
  ContentGuardrailCatalog,
  ContentGuardrailSettings,
  EmbeddingCatalog,
  EmbeddingSelectionResult,
  LlmCatalog,
  LlmDefaultUpdate,
  LlmProviderEntry,
  LlmProviderUpsert,
  LlmTestResult,
  MemoryCatalog,
  PermissionCatalog,
  PermissionMode,
  ScheduleCatalog,
  ScheduleCreateInput,
  ScheduledTask,
  ScheduleUpdateInput,
  SkillAnalytics,
  SkillCatalog,
  SkillCandidate,
  SkillRegistryItem,
  SkillDependency,
  SkillEvaluationReport,
  SkillExperiment,
  SkillLearningStatus,
  SkillPromotionResult,
  SkillUpgradeResult,
  SkillVersion,
  SkillVersionCompareResult,
} from "../types/settings";

const API_ROOT =
  import.meta.env.VITE_TRAJECTA_API_URL ??
  "http://127.0.0.1:8000/api/v1";

export class ApiError extends Error {
  status: number;
  detail: unknown;

  constructor(
    message: string,
    status: number,
    detail?: unknown,
  ) {
    super(message);

    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

async function readError(
  response: Response,
): Promise<ApiError> {
  let detail: unknown;

  try {
    detail = await response.json();
  } catch {
    detail = await response.text();
  }

  const message =
    typeof detail === "object" &&
    detail !== null &&
    "detail" in detail
      ? String(
          (detail as { detail: unknown }).detail,
        )
      : `Request failed (${response.status})`;

  return new ApiError(
    message,
    response.status,
    detail,
  );
}

async function request<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  const response = await fetch(
    `${API_ROOT}${path}`,
    {
      ...init,

      headers: {
        ...(init?.body instanceof FormData
          ? {}
          : {
              "Content-Type": "application/json",
            }),

        ...init?.headers,
      },
    },
  );

  if (!response.ok) {
    throw await readError(response);
  }

  return (await response.json()) as T;
}

async function streamRequest(
  path: string,
  body: unknown,
  signal: AbortSignal,
  onEvent: (
    event: ChatStreamEvent,
  ) => void | Promise<void>,
): Promise<void> {
  const response = await fetch(
    `${API_ROOT}${path}`,
    {
      method: "POST",

      headers: {
        "Content-Type": "application/json",
        Accept: "text/event-stream",
      },

      body: JSON.stringify(body),

      signal,
    },
  );

  if (!response.ok) {
    throw await readError(response);
  }

  await consumeSSE(
    response,
    async ({ event, data }) => {
      const payload = JSON.parse(data) as Omit<
        ChatStreamEvent,
        "type"
      >;

      await onEvent({
        type: event,
        ...payload,
      });
    },
  );
}

export const chatApi = {
  health(): Promise<HealthResponse> {
    return request("/health");
  },

  listConversations(): Promise<
    Conversation[]
  > {
    return request(
      "/chat/conversations",
    );
  },

  createConversation(body: {
    title?: string | null;
    model?: string | null;
    workspace_path?: string | null;
    metadata?: Record<string, unknown>;
  }): Promise<Conversation> {
    return request(
      "/chat/conversations",
      {
        method: "POST",
        body: JSON.stringify(body),
      },
    );
  },

  getConversation(
    id: string,
  ): Promise<ConversationDetail> {
    return request(
      `/chat/conversations/${id}`,
    );
  },

  listBranches(
    id: string,
  ): Promise<ChatBranch[]> {
    return request(
      `/chat/conversations/${id}/branches`,
    );
  },

  activateBranch(
    conversationId: string,
    branchId: string,
  ): Promise<ConversationDetail> {
    return request(
      `/chat/conversations/${conversationId}/branches/${branchId}/activate`,
      {
        method: "PUT",
      },
    );
  },

  selectModel(
    conversationId: string,
    model: string,
  ): Promise<Conversation> {
    return request(
      `/chat/conversations/${conversationId}/model`,
      {
        method: "PUT",

        body: JSON.stringify({
          model,
        }),
      },
    );
  },

  /** Pin a conversation to a host folder. The server validates the path. */
  selectWorkspace(
    conversationId: string,
    workspacePath: string,
  ): Promise<Conversation> {
    return request(
      `/chat/conversations/${conversationId}/workspace`,
      {
        method: "PUT",

        body: JSON.stringify({
          workspace_path: workspacePath,
        }),
      },
    );
  },

  listModels(): Promise<ModelCatalog> {
    return request("/models");
  },

  listMcpTools(): Promise<McpCatalog> {
    return request("/tools/mcp");
  },

  refreshMcpTools(): Promise<McpCatalog> {
    return request("/tools/mcp/refresh", {
      method: "POST",
    });
  },

  setMcpServerEnabled(
    serverName: string,
    enabled: boolean,
  ): Promise<McpCatalog> {
    return request(
      `/tools/mcp/servers/${encodeURIComponent(serverName)}`,
      {
        method: "PATCH",
        body: JSON.stringify({ enabled }),
      },
    );
  },

  setMcpToolEnabled(
    serverName: string,
    toolName: string,
    enabled: boolean,
  ): Promise<McpCatalog> {
    return request(
      `/tools/mcp/servers/${encodeURIComponent(
        serverName,
      )}/tools/${encodeURIComponent(toolName)}`,
      {
        method: "PATCH",
        body: JSON.stringify({ enabled }),
      },
    );
  },

  listMemories(
    tier: "semantic" | "episodic" | "procedural",
  ): Promise<SavedMemory[]> {
    return request(`/memory/${tier}`);
  },

  saveMemory(
    tier: "semantic",
    body: { key: string; content: string },
  ): Promise<{ ok: boolean; key: string; tier: string }> {
    return request(`/memory/${tier}`, {
      method: "POST",
      body: JSON.stringify(body),
    });
  },

  deleteMemory(
    tier: "semantic",
    key: string,
  ): Promise<{ ok: boolean; key: string; tier: string }> {
    return request(`/memory/${tier}/${encodeURIComponent(key)}`, {
      method: "DELETE",
    });
  },

  async uploadAttachments(
    conversationId: string,
    files: File[],
  ): Promise<Attachment[]> {
    const body = new FormData();

    for (const file of files) {
      body.append("files", file);
    }

    return request(
      `/chat/conversations/${conversationId}/attachments`,
      {
        method: "POST",
        body,
      },
    );
  },

  streamMessage(
    conversationId: string,
    body: {
      content: string;
      attachment_ids: string[];
      model?: string | null;
    },
    signal: AbortSignal,
    onEvent: (
      event: ChatStreamEvent,
    ) => void | Promise<void>,
  ) {
    return streamRequest(
      `/chat/conversations/${conversationId}/messages/stream`,
      body,
      signal,
      onEvent,
    );
  },

  streamEdit(
    conversationId: string,
    messageId: string,
    body: {
      content: string;
      attachment_ids?: string[] | null;
      model?: string | null;
    },
    signal: AbortSignal,
    onEvent: (
      event: ChatStreamEvent,
    ) => void | Promise<void>,
  ) {
    return streamRequest(
      `/chat/conversations/${conversationId}/messages/${messageId}/edit/stream`,
      body,
      signal,
      onEvent,
    );
  },

  streamResend(
    conversationId: string,
    messageId: string,
    body: {
      model?: string | null;
    },
    signal: AbortSignal,
    onEvent: (
      event: ChatStreamEvent,
    ) => void | Promise<void>,
  ) {
    return streamRequest(
      `/chat/conversations/${conversationId}/messages/${messageId}/resend/stream`,
      body,
      signal,
      onEvent,
    );
  },

  streamRegenerate(
    conversationId: string,
    messageId: string,
    body: {
      model?: string | null;
    },
    signal: AbortSignal,
    onEvent: (
      event: ChatStreamEvent,
    ) => void | Promise<void>,
  ) {
    return streamRequest(
      `/chat/conversations/${conversationId}/messages/${messageId}/regenerate/stream`,
      body,
      signal,
      onEvent,
    );
  },

  async getPendingApproval(
    conversationId: string,
  ): Promise<PendingApproval | null> {
    try {
      return await request(
        `/chat/conversations/${conversationId}/approval`,
      );
    } catch (error) {
      if (
        error instanceof ApiError &&
        error.status === 404
      ) {
        return null;
      }

      throw error;
    }
  },

  streamApproval(
    conversationId: string,
    decisions: ApprovalDecision[],
    signal: AbortSignal,
    onEvent: (
      event: ChatStreamEvent,
    ) => void | Promise<void>,
  ) {
    return streamRequest(
      `/chat/conversations/${conversationId}/approval/stream`,
      {
        decisions,
      },
      signal,
      onEvent,
    );
  },

  cancel(
    conversationId: string,
  ): Promise<{ cancelled: boolean }> {
    return request(
      `/chat/conversations/${conversationId}/cancel`,
      {
        method: "POST",
      },
    );
  },

  deleteConversation(
    conversationId: string,
  ): Promise<void> {
    return request(
      `/chat/conversations/${conversationId}`,
      {
        method: "DELETE",
      },
    );
  },

  attachmentContentUrl(
    conversationId: string,
    attachmentId: string,
  ): string {
    return (
      `${API_ROOT}/chat/conversations/` +
      `${conversationId}/attachments/` +
      `${attachmentId}/content`
    );
  },
};

export const settingsApi = {
  permissions(): Promise<PermissionCatalog> {
    return request("/permissions");
  },

  setPermission(
    permissionId: string,
    mode: PermissionMode,
  ): Promise<PermissionCatalog> {
    return request(
      `/permissions/${encodeURIComponent(permissionId)}`,
      {
        method: "PATCH",
        body: JSON.stringify({ mode }),
      },
    );
  },

  memory(query = ""): Promise<MemoryCatalog> {
    const params = new URLSearchParams();
    if (query) params.set("query", query);
    const qs = params.toString();
    return request(`/memory${qs ? `?${qs}` : ""}`);
  },

  setAutomaticMemory(
    automaticMemory: boolean,
  ): Promise<{ automatic_memory: boolean }> {
    return request("/memory/settings", {
      method: "PATCH",
      body: JSON.stringify({ automatic_memory: automaticMemory }),
    });
  },

  embedding(): Promise<EmbeddingCatalog> {
    return request("/memory/embedding");
  },

  setEmbeddingModel(
    model: string,
  ): Promise<EmbeddingSelectionResult> {
    return request("/memory/embedding", {
      method: "PATCH",
      body: JSON.stringify({ model }),
    });
  },

  /** Off falls back to the built-in default embedder; on reuses the last model. */
  setEmbeddingEnabled(
    enabled: boolean,
  ): Promise<EmbeddingSelectionResult> {
    return request("/memory/embedding", {
      method: "PATCH",
      body: JSON.stringify({ enabled }),
    });
  },

  /** Gateway status, defaults, and the provider catalog behind Bifrost. */
  llmCatalog(): Promise<LlmCatalog> {
    return request("/llm");
  },

  upsertLlmProvider(
    provider: string,
    body: LlmProviderUpsert,
  ): Promise<LlmProviderEntry> {
    return request(
      `/llm/providers/${encodeURIComponent(provider)}`,
      {
        method: "PUT",
        body: JSON.stringify(body),
      },
    );
  },

  deleteLlmProvider(
    provider: string,
  ): Promise<{ deleted: string }> {
    return request(
      `/llm/providers/${encodeURIComponent(provider)}`,
      { method: "DELETE" },
    );
  },

  llmProviderModels(
    provider: string,
  ): Promise<{ models: string[] }> {
    return request(
      `/llm/providers/${encodeURIComponent(provider)}/models`,
    );
  },

  testLlmProvider(
    provider: string,
  ): Promise<LlmTestResult> {
    return request(
      `/llm/providers/${encodeURIComponent(provider)}/test`,
      { method: "POST" },
    );
  },

  /** Persisted global default; applies to new conversations only. */
  setLlmDefault(
    body: LlmDefaultUpdate,
  ): Promise<LlmDefaultUpdate> {
    return request("/llm/default", {
      method: "PUT",
      body: JSON.stringify(body),
    });
  },

  guardrails(): Promise<ContentGuardrailCatalog> {
    return request("/guardrails");
  },

  setGuardrails(
    patch: Partial<ContentGuardrailSettings>,
  ): Promise<ContentGuardrailCatalog> {
    return request("/guardrails", {
      method: "PATCH",
      body: JSON.stringify(patch),
    });
  },

  saveMemory(
    key: string,
    content: string,
  ): Promise<{ ok: boolean; key: string; tier: string }> {
    return request("/memory/semantic", {
      method: "POST",
      body: JSON.stringify({ key, content }),
    });
  },

  deleteMemory(
    key: string,
  ): Promise<{ ok: boolean; key: string; tier: string }> {
    return request(`/memory/semantic/${encodeURIComponent(key)}`, {
      method: "DELETE",
    });
  },

  skills(): Promise<SkillCatalog> {
    return request("/skills");
  },

  async streamSkillEvaluation(
    candidateId: string,
    upgrade: boolean,
    onEvent: (event: SkillEvalEvent) => void,
    signal: AbortSignal,
  ): Promise<void> {
    const url = `${API_ROOT}/skills/candidates/${encodeURIComponent(candidateId)}/evaluate/stream?upgrade=${upgrade}`;
    const response = await fetch(url, {
      method: "POST",
      headers: { Accept: "text/event-stream" },
      signal,
    });
    if (!response.ok) throw await readError(response);
    await consumeSSE(response, ({ event, data }) => {
      onEvent({ ...(JSON.parse(data) as SkillEvalEvent), type: event });
    });
  },

  async followBackgroundSkillEvaluation(
    candidateId: string,
    onEvent: (event: SkillEvalEvent) => void,
    signal: AbortSignal,
  ): Promise<void> {
    const response = await fetch(
      `${API_ROOT}/skills/candidates/${encodeURIComponent(candidateId)}/evaluation/live`,
      { headers: { Accept: "text/event-stream" }, signal },
    );
    if (!response.ok) throw await readError(response);
    await consumeSSE(response, ({ event, data }) => {
      onEvent({ ...(JSON.parse(data) as SkillEvalEvent), type: event });
    });
  },

  evaluateSkill(
    candidateId: string,
  ): Promise<SkillEvaluationReport> {
    return request(
      `/skills/candidates/${encodeURIComponent(candidateId)}/evaluate`,
      { method: "POST" },
    );
  },

  promoteSkill(
    candidateId: string,
  ): Promise<SkillPromotionResult> {
    return request(
      `/skills/candidates/${encodeURIComponent(candidateId)}/promote`,
      { method: "POST" },
    );
  },

  rejectSkill(
    candidateId: string,
    reason: string,
  ): Promise<Record<string, unknown>> {
    return request(
      `/skills/candidates/${encodeURIComponent(candidateId)}/reject`,
      {
        method: "POST",
        body: JSON.stringify({ reason }),
      },
    );
  },

  setSkillEnabled(
    skillName: string,
    enabled: boolean,
  ): Promise<{ name: string; enabled: boolean }> {
    return request(
      `/skills/${encodeURIComponent(skillName)}/enabled`,
      {
        method: "PATCH",
        body: JSON.stringify({ enabled }),
      },
    );
  },

  skillVersions(
    skillName: string,
  ): Promise<SkillVersion[]> {
    return request(
      `/skills/${encodeURIComponent(skillName)}/versions`,
    );
  },

  compareSkillVersions(
    skillName: string,
    fromVersion: string,
    toVersion: string,
  ): Promise<SkillVersionCompareResult> {
    return request(
      `/skills/${encodeURIComponent(skillName)}/versions/compare`,
      {
        method: "POST",
        body: JSON.stringify({
          from_version: fromVersion,
          to_version: toVersion,
        }),
      },
    );
  },

  rollbackSkill(
    skillName: string,
    version: string,
    reason?: string,
  ): Promise<SkillPromotionResult> {
    return request(
      `/skills/${encodeURIComponent(skillName)}/rollback`,
      {
        method: "POST",
        body: JSON.stringify({ version, reason }),
      },
    );
  },

  /** Evaluate a candidate and promote it only when the evaluation passes. */
  upgradeSkill(
    candidateId: string,
    reason?: string,
  ): Promise<SkillUpgradeResult> {
    return request("/skills/upgrade", {
      method: "POST",
      body: JSON.stringify({
        candidate_id: candidateId,
        reason,
      }),
    });
  },

  learningStatus(): Promise<SkillLearningStatus> {
    return request("/skills/learning/status");
  },

  runSkillLearning(
    force = true,
  ): Promise<{ queued: boolean; status: SkillLearningStatus }> {
    return request("/skills/learning/run", {
      method: "POST",
      body: JSON.stringify({ force }),
    });
  },

  /** Per-version metrics, experiments and regressions for one skill. */
  skillAnalytics(skillName: string): Promise<SkillAnalytics> {
    return request(
      `/skills/${encodeURIComponent(skillName)}/analytics`,
    );
  },

  /** Run the automatic rollback policy for one skill. */
  checkSkillRegression(
    skillName: string,
  ): Promise<Record<string, unknown>> {
    return request(
      `/skills/${encodeURIComponent(skillName)}/regression/check`,
      { method: "POST" },
    );
  },

  experiments(skillName?: string): Promise<SkillExperiment[]> {
    const qs = skillName
      ? `?skill_name=${encodeURIComponent(skillName)}`
      : "";

    return request(`/skills/experiments${qs}`);
  },

  startSkillExperiment(
    candidateId: string,
    body: {
      strategy?: "ab" | "thompson";
      traffic_percent?: number;
      auto_stop?: boolean;
      auto_promote?: boolean;
    } = {},
  ): Promise<SkillExperiment> {
    return request(
      `/skills/candidates/${encodeURIComponent(candidateId)}/experiment`,
      {
        method: "POST",
        body: JSON.stringify(body),
      },
    );
  },

  addExperimentArm(
    experimentId: string,
    candidateId: string,
  ): Promise<SkillExperiment> {
    return request(
      `/skills/experiments/${encodeURIComponent(experimentId)}/arms`,
      {
        method: "POST",
        body: JSON.stringify({ candidate_id: candidateId }),
      },
    );
  },

  analyzeExperiment(
    experimentId: string,
  ): Promise<Record<string, unknown>> {
    return request(
      `/skills/experiments/${encodeURIComponent(experimentId)}/analysis`,
    );
  },

  stopExperiment(
    experimentId: string,
    reason: string,
  ): Promise<SkillExperiment> {
    return request(
      `/skills/experiments/${encodeURIComponent(experimentId)}/stop`,
      {
        method: "POST",
        body: JSON.stringify({ reason }),
      },
    );
  },

  skillDependencies(skillName: string): Promise<SkillDependency[]> {
    return request(
      `/skills/${encodeURIComponent(skillName)}/dependencies`,
    );
  },

  addSkillDependency(
    skillName: string,
    body: {
      depends_on_skill: string;
      version_constraint?: string;
      required?: boolean;
    },
  ): Promise<SkillDependency> {
    return request(
      `/skills/${encodeURIComponent(skillName)}/dependencies`,
      {
        method: "POST",
        body: JSON.stringify(body),
      },
    );
  },

  deleteSkillDependency(
    skillName: string,
    dependsOnSkill: string,
  ): Promise<{ deleted: boolean }> {
    return request(
      `/skills/${encodeURIComponent(skillName)}/dependencies/${encodeURIComponent(dependsOnSkill)}`,
      { method: "DELETE" },
    );
  },

  schedules(): Promise<ScheduleCatalog> {
    return request("/tasks/schedules");
  },

  createSchedule(
    body: ScheduleCreateInput,
  ): Promise<ScheduledTask> {
    return request("/tasks/schedules", {
      method: "POST",
      body: JSON.stringify(body),
    });
  },

  updateSchedule(
    scheduleId: string,
    body: ScheduleUpdateInput,
  ): Promise<ScheduledTask> {
    return request(
      `/tasks/schedules/${encodeURIComponent(scheduleId)}`,
      {
        method: "PATCH",
        body: JSON.stringify(body),
      },
    );
  },

  deleteSchedule(
    scheduleId: string,
  ): Promise<{ deleted: boolean; id: string }> {
    return request(
      `/tasks/schedules/${encodeURIComponent(scheduleId)}`,
      { method: "DELETE" },
    );
  },
};
export interface LearnedExperience {
  id: string;
  kind: "preference" | "correction" | "procedure";
  status: "active" | "needs_review" | "rejected";
  content: string;
  confidence: number;
  version: number;
  source_trajectory_id: string | null;
  evidence: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface LearningOverview {
  items: LearnedExperience[];
  skills: SkillRegistryItem[];
  previous_candidates: SkillCandidate[];
}

export const learningApi = {
  overview(): Promise<LearningOverview> {
    return request("/learning/overview");
  },
  list(): Promise<{ items: LearnedExperience[] }> {
    return request("/learning/experiences");
  },
  review(id: string, decision: "approve" | "reject"): Promise<LearnedExperience> {
    return request(`/learning/experiences/${encodeURIComponent(id)}/review`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ decision }),
    });
  },
  feedback(trajectoryId: string, rating: "success" | "failure", note = "") {
    return request("/learning/feedback", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ trajectory_id: trajectoryId, rating, note }),
    });
  },
  approveCandidate(id: string) {
    return request(`/skills/candidates/${encodeURIComponent(id)}/approve`, {
      method: "POST",
    });
  },
};
