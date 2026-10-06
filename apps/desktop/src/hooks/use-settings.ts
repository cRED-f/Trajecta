import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { settingsApi } from "../lib/api";

import type {
  ContentGuardrailCatalog,
  ContentGuardrailSettings,
  LlmDefaultUpdate,
  LlmProviderUpsert,
  PermissionCatalog,
  PermissionMode,
  ScheduleCreateInput,
  ScheduleUpdateInput,
} from "../types/settings";

export const settingsQueryKeys = {
  permissions: ["settings", "permissions"] as const,
  llm: ["settings", "llm"] as const,
  llmModels: (provider: string) =>
    ["settings", "llm", "models", provider] as const,
  memory: (query: string) => ["settings", "memory", query] as const,
  memoryAll: ["settings", "memory"] as const,
  embedding: ["settings", "embedding"] as const,
  guardrails: ["settings", "guardrails"] as const,
  skills: ["settings", "skills"] as const,
  skillVersions: (skillName: string) =>
    ["settings", "skills", "versions", skillName] as const,
  skillLearning: ["settings", "skills", "learning"] as const,
  skillAnalytics: (skillName: string) =>
    ["settings", "skills", "analytics", skillName] as const,
  skillDependencies: (skillName: string) =>
    ["settings", "skills", "dependencies", skillName] as const,
  experiments: ["settings", "skills", "experiments"] as const,
  schedules: ["settings", "schedules"] as const,
};

export function usePermissions(enabled = true) {
  return useQuery({
    queryKey: settingsQueryKeys.permissions,
    queryFn: settingsApi.permissions,
    enabled,
    staleTime: 5_000,
    refetchOnWindowFocus: false,
  });
}

export function usePermissionActions() {
  const queryClient = useQueryClient();

  function apply(catalog: PermissionCatalog) {
    queryClient.setQueryData(settingsQueryKeys.permissions, catalog);
  }

  const setMode = useMutation({
    mutationFn: ({
      permissionId,
      mode,
    }: {
      permissionId: string;
      mode: PermissionMode;
    }) => settingsApi.setPermission(permissionId, mode),
    onSuccess: apply,
  });

  return {
    setMode: setMode.mutateAsync,
    updating: setMode.isPending,
    pendingPermissionId: setMode.variables?.permissionId ?? null,
  };
}

export function useMemoryCatalog(query = "", enabled = true) {
  return useQuery({
    queryKey: settingsQueryKeys.memory(query),
    queryFn: () => settingsApi.memory(query),
    enabled,
    staleTime: 3_000,
    refetchOnWindowFocus: false,
  });
}

export function useMemoryActions() {
  const queryClient = useQueryClient();

  const setAutomaticMemory = useMutation({
    mutationFn: (automaticMemory: boolean) =>
      settingsApi.setAutomaticMemory(automaticMemory),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: settingsQueryKeys.memoryAll,
      });
    },
  });

  const addMemory = useMutation({
    mutationFn: ({ key, content }: { key: string; content: string }) =>
      settingsApi.saveMemory(key, content),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: settingsQueryKeys.memoryAll,
      });
    },
  });

  const deleteMemory = useMutation({
    mutationFn: (key: string) => settingsApi.deleteMemory(key),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: settingsQueryKeys.memoryAll,
      });
    },
  });

  return {
    setAutomaticMemory: setAutomaticMemory.mutateAsync,
    updatingAutomaticMemory: setAutomaticMemory.isPending,
    addMemory: addMemory.mutateAsync,
    savingMemory: addMemory.isPending,
    deleteMemory: deleteMemory.mutateAsync,
    deletingMemory: deleteMemory.isPending,
  };
}

/** Installed Ollama embedding models + the currently active one. */
export function useEmbeddingCatalog(enabled = true) {
  return useQuery({
    queryKey: settingsQueryKeys.embedding,
    queryFn: settingsApi.embedding,
    enabled,
    staleTime: 10_000,
    refetchOnWindowFocus: false,
  });
}

/** Switching embedding model rebuilds Qdrant, so it can take a while. */
export function useEmbeddingActions() {
  const queryClient = useQueryClient();

  function afterEmbeddingChange() {
    void queryClient.invalidateQueries({
      queryKey: settingsQueryKeys.embedding,
    });
    void queryClient.invalidateQueries({
      queryKey: settingsQueryKeys.memoryAll,
    });
  }

  const selectModel = useMutation({
    mutationFn: (model: string) => settingsApi.setEmbeddingModel(model),
    onSuccess: afterEmbeddingChange,
  });

  const setEnabled = useMutation({
    mutationFn: (enabled: boolean) => settingsApi.setEmbeddingEnabled(enabled),
    onSuccess: afterEmbeddingChange,
  });

  return {
    selectModel: selectModel.mutateAsync,
    selectingModel: selectModel.isPending,
    pendingModel: selectModel.variables ?? null,
    setEnabled: setEnabled.mutateAsync,
    togglingEnabled: setEnabled.isPending,
    error: selectModel.error ?? setEnabled.error,
  };
}

/** Gateway status, defaults, and providers behind Bifrost. */
export function useLlmCatalog(enabled = true) {
  return useQuery({
    queryKey: settingsQueryKeys.llm,
    queryFn: settingsApi.llmCatalog,
    enabled,
    staleTime: 5_000,
    refetchOnWindowFocus: false,
  });
}

/** Discovered models for one configured provider (404s otherwise). */
export function useLlmProviderModels(
  provider: string | null,
  enabled = true,
) {
  return useQuery({
    queryKey: settingsQueryKeys.llmModels(provider ?? ""),
    queryFn: () => settingsApi.llmProviderModels(provider ?? ""),
    enabled: Boolean(provider) && enabled,
    staleTime: 30_000,
    refetchOnWindowFocus: false,
    retry: false,
  });
}

export function useLlmActions() {
  const queryClient = useQueryClient();

  function invalidate() {
    void queryClient.invalidateQueries({
      queryKey: settingsQueryKeys.llm,
    });
  }

  const upsert = useMutation({
    mutationFn: ({
      provider,
      body,
    }: {
      provider: string;
      body: LlmProviderUpsert;
    }) => settingsApi.upsertLlmProvider(provider, body),
    onSuccess: invalidate,
  });

  const setDefault = useMutation({
    mutationFn: (body: LlmDefaultUpdate) =>
      settingsApi.setLlmDefault(body),
    onSuccess: invalidate,
  });

  const test = useMutation({
    mutationFn: (provider: string) =>
      settingsApi.testLlmProvider(provider),
    onSuccess: invalidate,
  });

  return {
    upsert: upsert.mutateAsync,
    upserting: upsert.isPending,
    setDefault: setDefault.mutateAsync,
    savingDefault: setDefault.isPending,
    test: test.mutateAsync,
    testingProvider: test.isPending ? test.variables ?? null : null,
    error: upsert.error ?? setDefault.error ?? test.error,
  };
}

export function useGuardrails(enabled = true) {
  return useQuery({
    queryKey: settingsQueryKeys.guardrails,
    queryFn: settingsApi.guardrails,
    enabled,
    staleTime: 5_000,
    refetchOnWindowFocus: false,
  });
}

export function useGuardrailActions() {
  const queryClient = useQueryClient();

  const update = useMutation({
    mutationFn: (patch: Partial<ContentGuardrailSettings>) =>
      settingsApi.setGuardrails(patch),
    onSuccess: (catalog: ContentGuardrailCatalog) => {
      queryClient.setQueryData(settingsQueryKeys.guardrails, catalog);
    },
  });

  return {
    update: update.mutateAsync,
    updating: update.isPending,
    error: update.error,
  };
}

export function useSkillCatalog(enabled = true) {
  return useQuery({
    queryKey: settingsQueryKeys.skills,
    queryFn: settingsApi.skills,
    enabled,
    staleTime: 5_000,
    refetchInterval: 15_000,
    refetchOnWindowFocus: false,
  });
}

/** Version history for one skill. Keyed under `skills` so a catalog
 * invalidation (promotion, rollback) refreshes it too. */
export function useSkillVersions(skillName: string | null, enabled = true) {
  return useQuery({
    queryKey: settingsQueryKeys.skillVersions(skillName ?? ""),
    queryFn: () => settingsApi.skillVersions(skillName ?? ""),
    enabled: Boolean(skillName) && enabled,
    staleTime: 5_000,
    refetchOnWindowFocus: false,
  });
}

/** Analytics for one skill. Nested under `skills` so a catalog
 * invalidation (promotion, rollback) refreshes it too. */
export function useSkillAnalytics(skillName: string | null, enabled = true) {
  return useQuery({
    queryKey: settingsQueryKeys.skillAnalytics(skillName ?? ""),
    queryFn: () => settingsApi.skillAnalytics(skillName ?? ""),
    enabled: Boolean(skillName) && enabled,
    staleTime: 5_000,
    refetchInterval: 30_000,
    refetchOnWindowFocus: false,
  });
}

export function useSkillLearningStatus(enabled = true) {
  return useQuery({
    queryKey: settingsQueryKeys.skillLearning,
    queryFn: settingsApi.learningStatus,
    enabled,
    staleTime: 5_000,
    refetchInterval: 15_000,
    refetchOnWindowFocus: false,
  });
}

/** Declared dependencies of one skill. */
export function useSkillDependencies(skillName: string | null, enabled = true) {
  return useQuery({
    queryKey: settingsQueryKeys.skillDependencies(skillName ?? ""),
    queryFn: () => settingsApi.skillDependencies(skillName ?? ""),
    enabled: Boolean(skillName) && enabled,
    staleTime: 5_000,
    refetchOnWindowFocus: false,
  });
}

/** Experiments across every skill (optionally narrowed to one). */
export function useSkillExperiments(
  skillName?: string,
  enabled = true,
) {
  return useQuery({
    queryKey: [...settingsQueryKeys.experiments, skillName ?? ""] as const,
    queryFn: () => settingsApi.experiments(skillName),
    enabled,
    staleTime: 5_000,
    refetchInterval: 15_000,
    refetchOnWindowFocus: false,
  });
}

export function useSkillActions() {
  const queryClient = useQueryClient();

  function invalidate() {
    void queryClient.invalidateQueries({
      queryKey: settingsQueryKeys.skills,
    });
  }

  const setEnabled = useMutation({
    mutationFn: ({
      skillName,
      enabled,
    }: {
      skillName: string;
      enabled: boolean;
    }) => settingsApi.setSkillEnabled(skillName, enabled),
    onSuccess: invalidate,
  });

  const evaluate = useMutation({
    mutationFn: (candidateId: string) =>
      settingsApi.evaluateSkill(candidateId),
    onSuccess: invalidate,
  });

  const promote = useMutation({
    mutationFn: (candidateId: string) =>
      settingsApi.promoteSkill(candidateId),
    onSuccess: invalidate,
  });

  const reject = useMutation({
    mutationFn: ({
      candidateId,
      reason,
    }: {
      candidateId: string;
      reason: string;
    }) => settingsApi.rejectSkill(candidateId, reason),
    onSuccess: invalidate,
  });

  const upgrade = useMutation({
    mutationFn: ({
      candidateId,
      reason,
    }: {
      candidateId: string;
      reason?: string;
    }) => settingsApi.upgradeSkill(candidateId, reason),
    onSuccess: invalidate,
  });

  const rollback = useMutation({
    mutationFn: ({
      skillName,
      version,
      reason,
    }: {
      skillName: string;
      version: string;
      reason?: string;
    }) => settingsApi.rollbackSkill(skillName, version, reason),
    onSuccess: invalidate,
  });

  const startExperiment = useMutation({
    mutationFn: ({
      candidateId,
      strategy,
    }: {
      candidateId: string;
      strategy?: "ab" | "thompson";
    }) =>
      settingsApi.startSkillExperiment(
        candidateId,
        strategy ? { strategy } : {},
      ),
    onSuccess: invalidate,
  });

  const addDependency = useMutation({
    mutationFn: ({
      skillName,
      body,
    }: {
      skillName: string;
      body: {
        depends_on_skill: string;
        version_constraint?: string;
        required?: boolean;
      };
    }) => settingsApi.addSkillDependency(skillName, body),
    onSuccess: invalidate,
  });

  const removeDependency = useMutation({
    mutationFn: ({
      skillName,
      dependsOnSkill,
    }: {
      skillName: string;
      dependsOnSkill: string;
    }) =>
      settingsApi.deleteSkillDependency(skillName, dependsOnSkill),
    onSuccess: invalidate,
  });

  return {
    setEnabled: setEnabled.mutateAsync,
    togglingEnabled: setEnabled.isPending,
    evaluate: evaluate.mutateAsync,
    evaluating: evaluate.isPending,
    promote: promote.mutateAsync,
    promoting: promote.isPending,
    reject: reject.mutateAsync,
    rejecting: reject.isPending,
    upgrade: upgrade.mutateAsync,
    upgrading: upgrade.isPending,
    rollback: rollback.mutateAsync,
    rollingBack: rollback.isPending,
    startExperiment: startExperiment.mutateAsync,
    startingExperiment: startExperiment.isPending,
    addDependency: addDependency.mutateAsync,
    addingDependency: addDependency.isPending,
    removeDependency: removeDependency.mutateAsync,
    removingDependency: removeDependency.isPending,
  };
}

/** Queueing a manual mining pass for the automatic learning loop. */
export function useSkillLearningActions() {
  const queryClient = useQueryClient();

  const run = useMutation({
    mutationFn: (force: boolean) => settingsApi.runSkillLearning(force),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: settingsQueryKeys.skillLearning,
      });
      void queryClient.invalidateQueries({
        queryKey: settingsQueryKeys.skills,
      });
    },
  });

  return {
    run: run.mutateAsync,
    running: run.isPending,
  };
}

export function useSchedules(enabled = true) {
  return useQuery({
    queryKey: settingsQueryKeys.schedules,
    queryFn: settingsApi.schedules,
    enabled,
    staleTime: 5_000,
    refetchInterval: 10_000,
    refetchOnWindowFocus: false,
  });
}

export function useScheduleActions() {
  const queryClient = useQueryClient();

  function invalidate() {
    void queryClient.invalidateQueries({
      queryKey: settingsQueryKeys.schedules,
    });
  }

  const create = useMutation({
    mutationFn: (body: ScheduleCreateInput) =>
      settingsApi.createSchedule(body),
    onSuccess: invalidate,
  });

  const update = useMutation({
    mutationFn: ({
      scheduleId,
      body,
    }: {
      scheduleId: string;
      body: ScheduleUpdateInput;
    }) => settingsApi.updateSchedule(scheduleId, body),
    onSuccess: invalidate,
  });

  const remove = useMutation({
    mutationFn: (scheduleId: string) =>
      settingsApi.deleteSchedule(scheduleId),
    onSuccess: invalidate,
  });

  return {
    create: create.mutateAsync,
    creating: create.isPending,
    update: update.mutateAsync,
    updating: update.isPending,
    remove: remove.mutateAsync,
    removing: remove.isPending,
  };
}