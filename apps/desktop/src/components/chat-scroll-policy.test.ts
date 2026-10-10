import { describe, expect, it } from "vitest";

import type { StreamState } from "../types/chat";
import { isAtScrollBottom, isLiveActivityGrowing, shouldPinChat } from "./chat-scroll-policy";

function stream(overrides: Partial<StreamState> = {}): StreamState {
  return {
    running: true,
    runId: "run-1",
    text: "",
    reasoning: "",
    tools: [],
    steps: [],
    warnings: [],
    error: null,
    ...overrides,
  };
}

describe("chat auto-scroll", () => {
  it("does not force the outer transcript to chase reasoning tokens", () => {
    expect(isLiveActivityGrowing(stream())).toBe(false);
    expect(shouldPinChat(false, isLiveActivityGrowing(stream()))).toBe(true);
    const thinking = stream({ reasoning: "one token" });
    expect(isLiveActivityGrowing(thinking)).toBe(true);
    expect(shouldPinChat(false, isLiveActivityGrowing(thinking))).toBe(false);
    expect(shouldPinChat(false, isLiveActivityGrowing(stream({ reasoning: "one token two tokens" })))).toBe(false);
  });

  it("does not chase expanding tool activity either", () => {
    expect(isLiveActivityGrowing(stream({
      tools: [{ key: "tool-1", name: "web_search", args: "query", status: "running" }],
    }))).toBe(true);
  });

  it("resumes following the final answer when text starts streaming", () => {
    const answering = stream({ reasoning: "thoughts", text: "Here is the answer" });
    expect(isLiveActivityGrowing(answering)).toBe(false);
    expect(shouldPinChat(false, isLiveActivityGrowing(answering))).toBe(true);
  });

  it("respects user scrolling even during the final answer", () => {
    expect(shouldPinChat(true, false)).toBe(false);
    expect(isLiveActivityGrowing(stream({ running: false, reasoning: "finished" }))).toBe(false);
  });
  it("follows reasoning only when its own viewport is at the latest text", () => {
    expect(isAtScrollBottom({ scrollHeight: 500, clientHeight: 180, scrollTop: 320 })).toBe(true);
    expect(isAtScrollBottom({ scrollHeight: 500, clientHeight: 180, scrollTop: 318 })).toBe(true);
    expect(isAtScrollBottom({ scrollHeight: 500, clientHeight: 180, scrollTop: 270 })).toBe(false);
    expect(isAtScrollBottom({ scrollHeight: 120, clientHeight: 180, scrollTop: 0 })).toBe(true);
  });

});
