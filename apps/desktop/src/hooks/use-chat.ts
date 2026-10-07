import { useQuery, useQueryClient } from "@tanstack/react-query";

import { chatApi } from "../lib/api";

import { useChatStore } from "../stores/chat-store";

import type {
  ApprovalDecision,
  Attachment,
  ChatMessage,
  ChatStreamEvent,
  Conversation,
  ConversationDetail,
} from "../types/chat";

const controllers = new Map<string, AbortController>();

export const queryKeys = {
  conversations: ["conversations"] as const,

  conversation: (id: string) => ["conversation", id] as const,

  branches: (id: string) => ["branches", id] as const,

  models: ["models"] as const,

  health: ["health"] as const,

  approval: (id: string) => ["approval", id] as const,

  memories: ["memories"] as const,
};

export function useConversationList() {
  return useQuery({
    queryKey: queryKeys.conversations,

    queryFn: chatApi.listConversations,

    refetchOnWindowFocus: true,
  });
}

export function useConversation(id: string | null) {
  return useQuery({
    queryKey: id ? queryKeys.conversation(id) : ["conversation", "none"],

    queryFn: () => chatApi.getConversation(id!),

    enabled: Boolean(id),
  });
}

export function useBranches(id: string | null) {
  return useQuery({
    queryKey: id ? queryKeys.branches(id) : ["branches", "none"],

    queryFn: () => chatApi.listBranches(id!),

    enabled: Boolean(id),
  });
}

export function useModels() {
  return useQuery({
    queryKey: queryKeys.models,

    queryFn: chatApi.listModels,

    staleTime: 30_000,
  });
}

export function usePendingApproval(
  id: string | null,
) {
  return useQuery({
    queryKey: id
      ? queryKeys.approval(id)
      : ["approval", "none"],

    queryFn: () =>
      chatApi.getPendingApproval(id!),

    enabled: Boolean(id),

    refetchOnWindowFocus: true,
  });
}

export function useSavedMemories() {
  return useQuery({
    queryKey: queryKeys.memories,

    queryFn: () => chatApi.listMemories("semantic"),

    refetchOnWindowFocus: true,
  });
}

export function useBackendHealth() {
  return useQuery({
    queryKey: queryKeys.health,

    queryFn: chatApi.health,

    retry: false,

    refetchInterval: 5_000,
  });
}

function messageAttachments(
  detail: ConversationDetail,
  message: ChatMessage,
): Attachment[] {
  return detail.attachments.filter(
    (attachment) => attachment.message_id === message.id,
  );
}

export function useChatActions() {
  const queryClient = useQueryClient();
  const clearStream = useChatStore((state) => state.clearStream);

  const activeConversationId = useChatStore(
    (state) => state.activeConversationId,
  );

  const setActiveConversation = useChatStore(
    (state) => state.setActiveConversation,
  );

  const draft = useChatStore((state) => state.draft);

  const files = useChatStore((state) => state.pendingFiles);

  const askQuote = useChatStore((state) => state.askQuote);

  const composeMode = useChatStore((state) => state.composeMode);

  const newConversationModel = useChatStore(
    (state) => state.newConversationModel,
  );

  const setNewConversationModel = useChatStore(
    (state) => state.setNewConversationModel,
  );

  const beginStream = useChatStore((state) => state.beginStream);

  const consumeEvent = useChatStore((state) => state.consumeStreamEvent);

  const failStream = useChatStore((state) => state.failStream);

  const stopStream = useChatStore((state) => state.stopStream);

  const resetComposer = useChatStore((state) => state.resetComposer);

  const cancelEdit = useChatStore((state) => state.cancelEdit);
  function appendOptimisticUserMessage(
    conversationId: string,
    content: string,
    uploaded: Attachment[],
  ): string {
    const id = `local-${crypto.randomUUID()}`;
    const createdAt = new Date().toISOString();

    const message: ChatMessage = {
      id,
      conversation_id: conversationId,
      role: "user",
      content,
      status: "pending",
      parent_message_id: null,
      revision_of: null,
      base_checkpoint_id: null,
      checkpoint_id: null,
      created_at: createdAt,
      metadata: {
        optimistic: true,
      },
    };

    queryClient.setQueryData<ConversationDetail>(
      queryKeys.conversation(conversationId),
      (previous) => {
        if (!previous) {
          return previous;
        }

        const uploadedIds = new Set(uploaded.map((item) => item.id));

        return {
          ...previous,

          updated_at: createdAt,

          messages: [...previous.messages, message],

          attachments: [
            ...previous.attachments.filter((item) => !uploadedIds.has(item.id)),

            ...uploaded.map((item) => ({
              ...item,
              message_id: id,
            })),
          ],
        };
      },
    );

    return id;
  }

  function reconcileAcceptedMessage(
    conversationId: string,
    optimisticMessageId: string,
    event: ChatStreamEvent,
  ) {
    const value = event.data.message;

    if (typeof value !== "object" || value === null || !("id" in value)) {
      return;
    }

    const accepted = value as ChatMessage;

    queryClient.setQueryData<ConversationDetail>(
      queryKeys.conversation(conversationId),

      (previous) => {
        if (!previous) {
          return previous;
        }

        const messages = previous.messages
          .filter((message) => message.id !== optimisticMessageId)
          .filter((message) => message.id !== accepted.id);

        messages.push(accepted);

        return {
          ...previous,

          messages,

          attachments: previous.attachments.map((attachment) =>
            attachment.message_id === optimisticMessageId
              ? {
                  ...attachment,
                  message_id: accepted.id,
                }
              : attachment,
          ),
        };
      },
    );
  }

  function markOptimisticMessageFailed(
    conversationId: string,
    optimisticMessageId: string,
  ) {
    queryClient.setQueryData<ConversationDetail>(
      queryKeys.conversation(conversationId),

      (previous) =>
        previous
          ? {
              ...previous,

              messages: previous.messages.map((message) =>
                message.id === optimisticMessageId
                  ? {
                      ...message,
                      status: "error" as const,
                    }
                  : message,
              ),
            }
          : previous,
    );
  }
  async function refresh(conversationId: string) {
    const detail = await chatApi.getConversation(conversationId);

    queryClient.setQueryData(queryKeys.conversation(conversationId), detail);

    await Promise.all([
      queryClient.invalidateQueries({
        queryKey: queryKeys.conversations,
      }),

      queryClient.invalidateQueries({
        queryKey: queryKeys.branches(conversationId),
      }),
    ]);

    return detail;
  }

  async function ensureConversation(): Promise<Conversation> {
    if (activeConversationId) {
      const cached = queryClient.getQueryData<ConversationDetail>(
        queryKeys.conversation(activeConversationId),
      );

      if (cached) {
        return cached;
      }

      return chatApi.getConversation(activeConversationId);
    }

    const catalog = queryClient.getQueryData<{
      default_model: string;
    }>(queryKeys.models);

    const conversation = await chatApi.createConversation({
      model: newConversationModel ?? catalog?.default_model ?? undefined,
    });

    setActiveConversation(conversation.id);

    queryClient.setQueryData(queryKeys.conversation(conversation.id), {
      ...conversation,
      branch: null,
      messages: [],
      attachments: [],
    } satisfies ConversationDetail);

    await queryClient.invalidateQueries({
      queryKey: queryKeys.conversations,
    });

    return conversation;
  }

  async function runStream(
    conversationId: string,

    execute: (
      signal: AbortSignal,
      onEvent: (event: ChatStreamEvent) => Promise<void>,
    ) => Promise<void>,

    options?: {
      optimisticMessageId?: string;
      preserveStream?: boolean;
    },
  ) {
    const existing = controllers.get(conversationId);

    if (existing) {
      throw new Error("This conversation is already running.");
    }

    const controller = new AbortController();

    controllers.set(conversationId, controller);

    beginStream(
      conversationId,
      options?.preserveStream ?? false,
    );

    let composerReset = false;

    try {
      await execute(
        controller.signal,

        async (event) => {
          consumeEvent(conversationId, event);

          if (event.type === "message.accepted") {
            if (!composerReset) {
              resetComposer();

              composerReset = true;
            }

            if (options?.optimisticMessageId) {
              reconcileAcceptedMessage(
                conversationId,
                options.optimisticMessageId,
                event,
              );

              await queryClient.invalidateQueries({
                queryKey: queryKeys.conversations,
              });
            } else {
              await refresh(conversationId);
            }
          }

          if (event.type === "message.completed") {
            await refresh(conversationId);

            clearStream(conversationId);

            await queryClient.invalidateQueries({
              queryKey: queryKeys.approval(conversationId),
            });
          }

          if (event.type === "run.interrupted") {
            await refresh(conversationId);

            await queryClient.invalidateQueries({
              queryKey: queryKeys.approval(conversationId),
            });
          }

          if (event.type === "run.error" || event.type === "run.cancelled") {
            await refresh(conversationId);
          }
        },
      );
    } catch (error) {
      if (options?.optimisticMessageId) {
        markOptimisticMessageFailed(
          conversationId,
          options.optimisticMessageId,
        );
      }
      if (error instanceof DOMException && error.name === "AbortError") {
        return;
      }

      failStream(
        conversationId,

        error instanceof Error ? error.message : "Chat request failed.",
      );

      throw error;
    } finally {
      controllers.delete(conversationId);
    }
  }

  async function send() {
    const typed = draft.trim();

    if (!typed && files.length === 0) {
      return;
    }

    // The "Ask Trajecta" selection rides along as a markdown quote so
    // the model sees exactly which passage is being asked about.
    const quoted = askQuote?.trim() ?? "";

    const content =
      quoted.length > 0
        ? `${quoted
            .split("\n")
            .map((line) => `> ${line}`)
            .join("\n")}${typed ? `\n\n${typed}` : ""}`
        : typed;

    const conversation = await ensureConversation();

    const conversationId = conversation.id;

    const uploaded =
      files.length > 0
        ? await chatApi.uploadAttachments(conversationId, files)
        : [];

    const model = conversation.model;

    if (composeMode.kind === "edit") {
      const detail = await chatApi.getConversation(conversationId);

      const existingAttachments = messageAttachments(
        detail,
        composeMode.message,
      );

      const attachmentIds =
        uploaded.length > 0
          ? [
              ...existingAttachments.map((item) => item.id),

              ...uploaded.map((item) => item.id),
            ]
          : null;

      await runStream(
        conversationId,

        (signal, onEvent) =>
          chatApi.streamEdit(
            conversationId,
            composeMode.message.id,

            {
              content,
              attachment_ids: attachmentIds,
              model,
            },

            signal,
            onEvent,
          ),
      );

      return;
    }

    const optimisticMessageId =
      appendOptimisticUserMessage(
        conversationId,
        content,
        uploaded,
      );

    await runStream(
      conversationId,

      (signal, onEvent) =>
        chatApi.streamMessage(
          conversationId,

          {
            content,
            attachment_ids: uploaded.map((item) => item.id),
            model,
          },

          signal,
          onEvent,
        ),

      {
        optimisticMessageId,
      },
    );
  }

  async function resend(message: ChatMessage) {
    if (!activeConversationId) {
      return;
    }

    const detail = await chatApi.getConversation(activeConversationId);

    await runStream(
      activeConversationId,

      (signal, onEvent) =>
        chatApi.streamResend(
          activeConversationId,
          message.id,

          {
            model: detail.model,
          },

          signal,
          onEvent,
        ),
    );
  }

  async function regenerate(message: ChatMessage) {
    if (!activeConversationId) {
      return;
    }

    const detail = await chatApi.getConversation(activeConversationId);

    await runStream(
      activeConversationId,

      (signal, onEvent) =>
        chatApi.streamRegenerate(
          activeConversationId,
          message.id,

          {
            model: detail.model,
          },

          signal,
          onEvent,
        ),
    );
  }

  async function resumeApproval(
    decisions: ApprovalDecision[],
  ) {
    if (!activeConversationId) {
      return;
    }

    const conversationId =
      activeConversationId;

    // Close the permission window immediately.
    // If resume fails, we refetch and show it again.
    queryClient.setQueryData(
      queryKeys.approval(conversationId),
      null,
    );

    try {
      await runStream(
        conversationId,

        (signal, onEvent) =>
          chatApi.streamApproval(
            conversationId,
            decisions,
            signal,
            onEvent,
          ),

        {
          preserveStream: true,
        },
      );
    } catch (error) {
      await queryClient.invalidateQueries({
        queryKey:
          queryKeys.approval(
            conversationId,
          ),
      });

      throw error;
    }
  }

  async function saveMemory(
    key: string,
    content: string,
  ) {
    await chatApi.saveMemory("semantic", { key, content });

    await queryClient.invalidateQueries({
      queryKey: queryKeys.memories,
    });
  }

  async function deleteMemory(
    key: string,
  ) {
    await chatApi.deleteMemory("semantic", key);

    await queryClient.invalidateQueries({
      queryKey: queryKeys.memories,
    });
  }

  async function cancel() {
    if (!activeConversationId) {
      return;
    }

    // Flip `running` off first so the UI reacts to Stop on this tick,
    // even if the backend call or the SSE abort takes a moment.
    stopStream(activeConversationId);

    const cancelRequest = chatApi.cancel(activeConversationId).catch(
      (error) => {
        console.error("Failed to cancel backend run:", error);
      },
    );

    controllers.get(activeConversationId)?.abort();

    controllers.delete(activeConversationId);

    await cancelRequest;

    await queryClient.invalidateQueries({
      queryKey: queryKeys.conversation(activeConversationId),
    });
  }

  async function activateBranch(branchId: string) {
    if (!activeConversationId) {
      return;
    }

    const detail = await chatApi.activateBranch(activeConversationId, branchId);

    queryClient.setQueryData(
      queryKeys.conversation(activeConversationId),
      detail,
    );

    await queryClient.invalidateQueries({
      queryKey: queryKeys.branches(activeConversationId),
    });
  }

  async function selectModel(model: string) {
    if (!activeConversationId) {
      setNewConversationModel(model);

      return;
    }

    const conversation = await chatApi.selectModel(activeConversationId, model);

    queryClient.setQueryData<ConversationDetail>(
      queryKeys.conversation(activeConversationId),

      (previous) =>
        previous
          ? {
              ...previous,
              model: conversation.model,
            }
          : previous,
    );

    await queryClient.invalidateQueries({
      queryKey: queryKeys.conversations,
    });
  }

  function newChat() {
    controllers.get(activeConversationId ?? "")?.abort();

    setActiveConversation(null);
    cancelEdit();
  }

  async function deleteConversation(
    conversationId: string,
  ) {
    await chatApi.deleteConversation(
      conversationId,
    );

    await queryClient.invalidateQueries({
      queryKey: queryKeys.conversations,
    });

    if (activeConversationId === conversationId) {
      setActiveConversation(null);
      cancelEdit();
    }
  }

  return {
    send,
    resend,
    regenerate,
    resumeApproval,
    cancel,
    activateBranch,
    selectModel,
    newChat,
    deleteConversation,
    saveMemory,
    deleteMemory,
  };
}
