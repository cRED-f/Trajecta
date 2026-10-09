import { describe, expect, it, vi, beforeEach } from "vitest";
import { learningMemoryApi } from "./learning-memory-api";
import { request } from "./api";

vi.mock("./api", () => ({ request: vi.fn(async () => ({ ok: true })) }));

const mocked = vi.mocked(request);

describe("learning-memory HTTP contracts", () => {
  beforeEach(() => mocked.mockClear());

  it("encodes workspace paths rather than falling back to all episodes", async () => {
    await learningMemoryApi.episodes("C:\\Projects\\Project & A");
    expect(mocked).toHaveBeenCalledWith(
      "/memory/episodic?workspace_path=C%3A%5CProjects%5CProject+%26+A&limit=100",
    );
    await learningMemoryApi.episodes(null);
    expect(mocked).toHaveBeenLastCalledWith("/memory/episodic?limit=100");
  });

  it("does not omit scoped identifiers from review and deletion", async () => {
    await learningMemoryApi.review("draft/one", "reject", "/one two");
    expect(mocked).toHaveBeenLastCalledWith(
      "/learning/procedures/draft%2Fone/review?workspace_path=%2Fone+two",
      { method: "POST", body: JSON.stringify({ decision: "reject" }) },
    );
    await learningMemoryApi.deleteEpisode("a/b", "/one two");
    expect(mocked).toHaveBeenLastCalledWith(
      "/memory/episodic/a%2Fb?workspace_path=%2Fone+two", { method: "DELETE" },
    );
  });

  it("sends explicit trajectory feedback without claiming automatic verification", async () => {
    await learningMemoryApi.feedback("run-1", "failure", "Result contained an error");
    expect(mocked).toHaveBeenLastCalledWith("/learning/feedback", {
      method: "POST",
      body: JSON.stringify({ trajectory_id: "run-1", rating: "failure", note: "Result contained an error" }),
    });
  });

  it("updates only reflection configuration, never the global chat model", async () => {
    await learningMemoryApi.setReflectionSettings({ model: "ollama/review:8b", max_daily_reviews: 5 });
    expect(mocked).toHaveBeenLastCalledWith("/learning/reflection/settings", {
      method: "PATCH", body: JSON.stringify({ model: "ollama/review:8b", max_daily_reviews: 5 }),
    });
  });

  it("manual curator and conflict actions require explicit requests", async () => {
    await learningMemoryApi.scan(null);
    expect(mocked).toHaveBeenLastCalledWith("/memory/curator/scan", { method: "POST" });
    await learningMemoryApi.resolve(4, "semantic:local");
    expect(mocked).toHaveBeenLastCalledWith("/memory/unified/conflicts/4/resolve", {
      method: "POST", body: JSON.stringify({ preferred_ref: "semantic:local" }),
    });
  });
});
