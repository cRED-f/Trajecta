import { consumeSSE } from "./sse";

import type {
  Attachment,
  ChatBranch,
  ChatStreamEvent,
  Conversation,
  ConversationDetail,
  HealthResponse,
  ModelCatalog,
} from "../types/chat";

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

  listModels(): Promise<ModelCatalog> {
    return request("/models");
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