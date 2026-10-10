import { afterEach, describe, expect, it, vi } from "vitest";
import { chatApi, request } from "./api";

afterEach(() => vi.unstubAllGlobals());

describe("local FastAPI fetch diagnostics", () => {
  it("replaces opaque fetch failures with backend diagnostics for REST requests", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    await expect(request("/health")).rejects.toThrow(
      /Cannot connect to Trajecta FastAPI.*Desktop & Application/,
    );
  });

  it("also diagnoses network failures before an SSE stream begins", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    await expect(chatApi.streamMessage(
      "conversation-1",
      { content: "hi", attachment_ids: [] },
      new AbortController().signal,
      () => {},
    )).rejects.toThrow(/Cannot connect to Trajecta FastAPI/);
  });

  it("does not turn a normal Stop cancellation into a connection failure", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new DOMException("Stopped", "AbortError")));
    await expect(chatApi.streamMessage(
      "conversation-1",
      { content: "hi", attachment_ids: [] },
      new AbortController().signal,
      () => {},
    )).rejects.toMatchObject({ name: "AbortError" });
  });
});
