import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { chatApi } from "../lib/api";

import type { McpCatalog } from "../types/tools";

export const toolsQueryKeys = {
  mcp: ["tools", "mcp"] as const,
};

export function useMcpCatalog(enabled = true) {
  return useQuery({
    queryKey: toolsQueryKeys.mcp,
    queryFn: chatApi.listMcpTools,
    enabled,
    staleTime: 10_000,
    refetchOnWindowFocus: false,
  });
}

export function useMcpActions() {
  const queryClient = useQueryClient();

  function apply(catalog: McpCatalog) {
    queryClient.setQueryData(toolsQueryKeys.mcp, catalog);
  }

  const refresh = useMutation({
    mutationFn: chatApi.refreshMcpTools,
    onSuccess: apply,
  });

  const setServer = useMutation({
    mutationFn: ({
      server,
      enabled,
    }: {
      server: string;
      enabled: boolean;
    }) => chatApi.setMcpServerEnabled(server, enabled),
    onSuccess: apply,
  });

  const setTool = useMutation({
    mutationFn: ({
      server,
      tool,
      enabled,
    }: {
      server: string;
      tool: string;
      enabled: boolean;
    }) => chatApi.setMcpToolEnabled(server, tool, enabled),
    onSuccess: apply,
  });

  return {
    refresh: refresh.mutateAsync,
    refreshing: refresh.isPending,
    setServer: setServer.mutateAsync,
    updatingServer: setServer.isPending,
    setTool: setTool.mutateAsync,
    updatingTool: setTool.isPending,
  };
}