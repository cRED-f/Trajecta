import { describe, expect, it } from "vitest";
import { chooseOllamaNotice, initialStartupProgress, startupSteps } from "./startup";

describe("cold-start model warnings", () => {
  it("does not require Ollama if no local model was selected", () => {
    expect(chooseOllamaNotice({ enabled: false, selected_model: null, ollama: { reachable: false } }, null, null)).toBeNull();
  });

  it("prompts to open Ollama when an enabled embedding model is unreachable", () => {
    const notice = chooseOllamaNotice({ enabled: true, selected_model: "nomic-embed-text", ollama: { reachable: false } }, null, null);
    expect(notice?.id).toBe("ollama-offline");
    expect(notice?.message).toContain("Open Ollama");
  });

  it("warns when the persisted background model fails warmup", () => {
    const notice = chooseOllamaNotice(
      { enabled: false, selected_model: null, ollama: { reachable: true } },
      "ollama/qwen3:8b",
      { embedding: { state: "skipped" }, learning: { state: "unavailable", model: "qwen3:8b" } },
    );
    expect(notice?.id).toBe("ollama-model");
  });

  it("keeps remote background models independent of local Ollama", () => {
    expect(chooseOllamaNotice(null, "9router/claude-opus", null)).toBeNull();
  });

  it("defines one progress state for every startup operation", () => {
    const states = initialStartupProgress();
    expect(startupSteps.map(({ id }) => states[id])).toEqual(Array(startupSteps.length).fill("pending"));
  });
});
