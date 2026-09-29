import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { settingsApi } from "../lib/api";

import type {
  PermissionCatalog,
  PermissionMode,
  ScheduleCreateInput,
  ScheduleUpdateInput,
} from "../types/settings";

export const settingsQueryKeys = {
  permissions: ["settings", "permissions"] as const,
  memory: (query: string) => ["settings", "memory", query] as const,
  memoryAll: ["settings", "memory"] as const,
  skills: ["settings", "skills"] as const,
  skillVersions: (skillName: string) =>
    ["settings", "skills", "versions", skillName] as const,
  skillLearning: ["settings", "skills", "learning"] as const,
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

export function useSkillLearningStatus(enabled = true) {
  return useQuery({
    queryKey: settingsQueryKeys.skillLearning,
    queryFn: settingsApi.skillLearningStatus,
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