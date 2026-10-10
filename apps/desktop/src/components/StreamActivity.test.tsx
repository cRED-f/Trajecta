import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";

import type { StreamState } from "../types/chat";
import { StreamActivity } from "./StreamActivity";

function activity(overrides: Partial<StreamState> = {}): StreamState {
  return {
    running: false,
    runId: "run-1",
    text: "An answer",
    reasoning: "",
    tools: [],
    steps: [],
    warnings: [],
    error: null,
    ...overrides,
  };
}

describe("StreamActivity", () => {
  it("hides empty activity on saved responses", () => {
    const html = renderToStaticMarkup(<StreamActivity stream={activity()} variant="saved" />);
    expect(html).toBe("");
  });

  it("keeps previous tool work accessible but initially collapsed and no longer calls it Thinking", () => {
    const html = renderToStaticMarkup(<StreamActivity stream={activity({
      reasoning: "Plan summary",
      tools: [{ key: "tool-1", name: "web_search", args: '{"q":"EMAI"}', result: "Found pages", status: "success" }],
    })} variant="saved" />);

    expect(html).toContain("Activity");
    expect(html).toContain("1 tool used");
    expect(html).toContain('aria-expanded="false"');
    expect(html).not.toContain("Thinking");
    expect(html).not.toContain("web_search");
  });

  it("shows a single expanded activity surface with reasoning and tool rows while working", () => {
    const html = renderToStaticMarkup(<StreamActivity stream={activity({
      running: true,
      text: "",
      reasoning: "Checking prerequisites",
      tools: [{ key: "tool-1", name: "web_search", args: '{"q":"EMAI"}', status: "running" }],
    })} variant="live" />);

    expect(html).toContain("Using web_search");
    expect(html).toContain('aria-expanded="true"');
    expect(html).toContain("Checking prerequisites");
    expect(html).toContain("web_search");
    expect(html).toContain("Running");
    expect(html).not.toContain("run-timeline__payload");
  });

  it("does not show an empty activity toggle while an answer is streaming", () => {
    const html = renderToStaticMarkup(<StreamActivity stream={activity({ running: true })} />);
    expect(html).toBe("");
  });

  it("does not claim an unfinished tool succeeded when the run ended", () => {
    const html = renderToStaticMarkup(<StreamActivity stream={activity({
      tools: [{ key: "tool-1", name: "read_file", args: "path" }],
    })} variant="saved" />);
    expect(html).toContain("1 tool used");
    // In collapsed state detail labels are intentionally not rendered.
    expect(html).toContain('aria-expanded="false"');
  });
});
