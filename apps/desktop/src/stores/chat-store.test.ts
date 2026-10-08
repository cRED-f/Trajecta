import { describe, expect, it } from "vitest";

import type { ChatStreamEvent } from "../types/chat";

import { restoreActivityEvents, useChatStore } from "./chat-store";

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

describe("model-provided reasoning", () => {
  it("streams reasoning independently of final answer text", () => {
    const id = "conv-reasoning";
    useChatStore.getState().beginStream(id);
    useChatStore.getState().consumeStreamEvent(id, {
      type: "reasoning.delta", conversation_id: id, run_id: "run-1",
      data: { source: "main", text: "Checking the input." },
    });
    const stream = useChatStore.getState().streams[id];
    expect(stream?.reasoning).toBe("Checking the input.");
    expect(stream?.text).toBe("");
  });

  it("restores reasoning from saved activity without fabricating any", () => {
    const restored = restoreActivityEvents([
      { type: "reasoning.delta", data: { source: "main", text: "Summary." } },
    ]);
    expect(restored?.reasoning).toBe("Summary.");
    expect(restoreActivityEvents([{ type: "agent.step", data: { node: "model" } }])?.reasoning).toBe("");
  });
});
