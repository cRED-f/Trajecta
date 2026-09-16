import {
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

import { chatApi } from "../lib/api";

import { useChatStore } from "../stores/chat-store";

import type {
  Attachment,
  ChatMessage,
  ChatStreamEvent,
  Conversation,
  ConversationDetail,
} from "../types/chat";

const controllers = new Map<
  string,
  AbortController
>();

export const queryKeys = {
  conversations: [
    "conversations",
  ] as const,

  conversation: (id: string) =>
    [
      "conversation",
      id,
    ] as const,

  branches: (id: string) =>
    [
      "branches",
      id,
    ] as const,

  models: ["models"] as const,

  health: ["health"] as const,
};

export function useConversationList() {
  return useQuery({
    queryKey:
      queryKeys.conversations,

    queryFn:
      chatApi.listConversations,

    refetchOnWindowFocus: true,
  });
}

export function useConversation(
  id: string | null,
) {
  return useQuery({
    queryKey: id
      ? queryKeys.conversation(id)
      : ["conversation", "none"],

    queryFn: () =>
      chatApi.getConversation(id!),

    enabled: Boolean(id),
  });
}

export function useBranches(
  id: string | null,
) {
  return useQuery({
    queryKey: id
      ? queryKeys.branches(id)
      : ["branches", "none"],

    queryFn: () =>
      chatApi.listBranches(id!),

    enabled: Boolean(id),
  });
}

export function useModels() {
  return useQuery({
    queryKey: queryKeys.models,

    queryFn:
      chatApi.listModels,

    staleTime: 30_000,
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
    (attachment) =>
      attachment.message_id ===
      message.id,
  );
}

export function useChatActions() {
  const queryClient =
    useQueryClient();

  const activeConversationId =
    useChatStore(
      (state) =>
        state.activeConversationId,
    );

  const setActiveConversation =
    useChatStore(
      (state) =>
        state.setActiveConversation,
    );

  const draft = useChatStore(
    (state) => state.draft,
  );

  const files = useChatStore(
    (state) =>
      state.pendingFiles,
  );

  const composeMode =
    useChatStore(
      (state) =>
        state.composeMode,
    );

  const newConversationModel =
    useChatStore(
      (state) =>
        state.newConversationModel,
    );

  const setNewConversationModel =
    useChatStore(
      (state) =>
        state.setNewConversationModel,
    );

  const beginStream =
    useChatStore(
      (state) =>
        state.beginStream,
    );

  const consumeEvent =
    useChatStore(
      (state) =>
        state.consumeStreamEvent,
    );

  const failStream =
    useChatStore(
      (state) =>
        state.failStream,
    );

  const resetComposer =
    useChatStore(
      (state) =>
        state.resetComposer,
    );

  const cancelEdit =
    useChatStore(
      (state) =>
        state.cancelEdit,
    );

  async function refresh(
    conversationId: string,
  ) {
    const detail =
      await chatApi.getConversation(
        conversationId,
      );

    queryClient.setQueryData(
      queryKeys.conversation(
        conversationId,
      ),
      detail,
    );

    await Promise.all([
      queryClient.invalidateQueries({
        queryKey:
          queryKeys.conversations,
      }),

      queryClient.invalidateQueries({
        queryKey:
          queryKeys.branches(
            conversationId,
          ),
      }),
    ]);

    return detail;
  }

  async function ensureConversation(): Promise<Conversation> {
    if (activeConversationId) {
      const cached =
        queryClient.getQueryData<
          ConversationDetail
        >(
          queryKeys.conversation(
            activeConversationId,
          ),
        );

      if (cached) {
        return cached;
      }

      return chatApi.getConversation(
        activeConversationId,
      );
    }

    const catalog =
      queryClient.getQueryData<{
        default_model: string;
      }>(queryKeys.models);

    const conversation =
      await chatApi.createConversation(
        {
          model:
            newConversationModel ??
            catalog?.default_model ??
            undefined,
        },
      );

    setActiveConversation(
      conversation.id,
    );

    queryClient.setQueryData(
      queryKeys.conversation(
        conversation.id,
      ),
      {
        ...conversation,
        branch: null,
        messages: [],
        attachments: [],
      } satisfies ConversationDetail,
    );

    await queryClient.invalidateQueries({
      queryKey:
        queryKeys.conversations,
    });

    return conversation;
  }

  async function runStream(
    conversationId: string,
    execute: (
      signal: AbortSignal,
      onEvent: (
        event: ChatStreamEvent,
      ) => Promise<void>,
    ) => Promise<void>,
  ) {
    const existing =
      controllers.get(
        conversationId,
      );

    if (existing) {
      throw new Error(
        "This conversation is already running.",
      );
    }

    const controller =
      new AbortController();

    controllers.set(
      conversationId,
      controller,
    );

    beginStream(
      conversationId,
    );

    let composerReset = false;

    try {
      await execute(
        controller.signal,

        async (event) => {
          consumeEvent(
            conversationId,
            event,
          );

          if (
            event.type ===
            "message.accepted"
          ) {
            if (!composerReset) {
              resetComposer();
              composerReset = true;
            }

            await refresh(
              conversationId,
            );
          }

          if (
            event.type ===
            "message.completed"
          ) {
            await refresh(
              conversationId,
            );
          }

          if (
            event.type ===
              "run.error" ||
            event.type ===
              "run.cancelled"
          ) {
            await refresh(
              conversationId,
            );
          }
        },
      );
    } catch (error) {
      if (
        error instanceof DOMException &&
        error.name ===
          "AbortError"
      ) {
        return;
      }

      failStream(
        conversationId,

        error instanceof Error
          ? error.message
          : "Chat request failed.",
      );

      throw error;
    } finally {
      controllers.delete(
        conversationId,
      );
    }
  }

  async function send() {
    const content = draft.trim();

    if (
      !content &&
      files.length === 0
    ) {
      return;
    }

    const conversation =
      await ensureConversation();

    const conversationId =
      conversation.id;

    const uploaded =
      files.length > 0
        ? await chatApi.uploadAttachments(
            conversationId,
            files,
          )
        : [];

    const model =
      conversation.model;

    if (
      composeMode.kind ===
      "edit"
    ) {
      const detail =
        await chatApi.getConversation(
          conversationId,
        );

      const existingAttachments =
        messageAttachments(
          detail,
          composeMode.message,
        );

      const attachmentIds =
        uploaded.length > 0
          ? [
              ...existingAttachments.map(
                (item) =>
                  item.id,
              ),

              ...uploaded.map(
                (item) =>
                  item.id,
              ),
            ]
          : null;

      await runStream(
        conversationId,

        (
          signal,
          onEvent,
        ) =>
          chatApi.streamEdit(
            conversationId,
            composeMode.message.id,

            {
              content,
              attachment_ids:
                attachmentIds,
              model,
            },

            signal,
            onEvent,
          ),
      );

      return;
    }

    await runStream(
      conversationId,

      (
        signal,
        onEvent,
      ) =>
        chatApi.streamMessage(
          conversationId,

          {
            content,
            attachment_ids:
              uploaded.map(
                (item) =>
                  item.id,
              ),
            model,
          },

          signal,
          onEvent,
        ),
    );
  }

  async function resend(
    message: ChatMessage,
  ) {
    if (!activeConversationId) {
      return;
    }

    const detail =
      await chatApi.getConversation(
        activeConversationId,
      );

    await runStream(
      activeConversationId,

      (
        signal,
        onEvent,
      ) =>
        chatApi.streamResend(
          activeConversationId,
          message.id,

          {
            model:
              detail.model,
          },

          signal,
          onEvent,
        ),
    );
  }

  async function regenerate(
    message: ChatMessage,
  ) {
    if (!activeConversationId) {
      return;
    }

    const detail =
      await chatApi.getConversation(
        activeConversationId,
      );

    await runStream(
      activeConversationId,

      (
        signal,
        onEvent,
      ) =>
        chatApi.streamRegenerate(
          activeConversationId,
          message.id,

          {
            model:
              detail.model,
          },

          signal,
          onEvent,
        ),
    );
  }

  async function cancel() {
    if (!activeConversationId) {
      return;
    }

    await chatApi.cancel(
      activeConversationId,
    );

    controllers
      .get(activeConversationId)
      ?.abort();

    controllers.delete(
      activeConversationId,
    );
  }

  async function activateBranch(
    branchId: string,
  ) {
    if (!activeConversationId) {
      return;
    }

    const detail =
      await chatApi.activateBranch(
        activeConversationId,
        branchId,
      );

    queryClient.setQueryData(
      queryKeys.conversation(
        activeConversationId,
      ),
      detail,
    );

    await queryClient.invalidateQueries({
      queryKey:
        queryKeys.branches(
          activeConversationId,
        ),
    });
  }

  async function selectModel(
    model: string,
  ) {
    if (!activeConversationId) {
      setNewConversationModel(
        model,
      );

      return;
    }

    const conversation =
      await chatApi.selectModel(
        activeConversationId,
        model,
      );

    queryClient.setQueryData<
      ConversationDetail
    >(
      queryKeys.conversation(
        activeConversationId,
      ),

      (previous) =>
        previous
          ? {
              ...previous,
              model:
                conversation.model,
            }
          : previous,
    );

    await queryClient.invalidateQueries({
      queryKey:
        queryKeys.conversations,
    });
  }

  function newChat() {
    controllers
      .get(
        activeConversationId ?? "",
      )
      ?.abort();

    setActiveConversation(null);
    cancelEdit();
  }

  return {
    send,
    resend,
    regenerate,
    cancel,
    activateBranch,
    selectModel,
    newChat,
  };
}