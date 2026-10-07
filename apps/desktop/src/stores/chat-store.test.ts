import { describe, expect, it } from "vitest";

import type { ChatStreamEvent } from "../types/chat";

import { useChatStore } from "./chat-store";

function delta(conversationId: string, text: string): ChatStreamEvent {
  return {
    type: "message.delta",
    conversation_id: conversationId,
    run_id: "run-1",
    data: { source: "main", text },
  };
}

describe("stopStream", () => {
  it("sets running to false immediately when Stop is pressed", () => {
    const conversationId = "conv-stop";

    useChatStore.getState().beginStream(conversationId);
    expect(useChatStore.getState().streams[conversationId]?.running).toBe(
      true,
    );

    // The UI must react on this tick, before the backend cancel or the
    // SSE abort resolves.
    useChatStore.getState().stopStream(conversationId);

    expect(useChatStore.getState().streams[conversationId]?.running).toBe(
      false,
    );
  });

  it("preserves already-streamed text and clears no other state", () => {
    const conversationId = "conv-keep-text";

    useChatStore.getState().beginStream(conversationId);
    useChatStore
      .getState()
      .consumeStreamEvent(conversationId, delta(conversationId, "partial"));

    useChatStore.getState().stopStream(conversationId);

    const stream = useChatStore.getState().streams[conversationId];
    expect(stream?.running).toBe(false);
    expect(stream?.text).toBe("partial");
    expect(stream?.error).toBeNull();
  });

  it("is a no-op for an unknown conversation", () => {
    expect(() => useChatStore.getState().stopStream("missing")).not.toThrow();
  });
});
