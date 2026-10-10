import { describe, expect, it } from "vitest";
import { providerForReflectionModel, qualifyReflectionModel } from "./reflection-model";

describe("reflection model persistence helpers", () => {
  it("restores the provider from a saved model, including nested model names", () => {
    expect(providerForReflectionModel("ollama/qwen3:8b")).toBe("ollama");
    expect(providerForReflectionModel("9router/meta-llama/llama-3.3-70b")).toBe("9router");
    expect(providerForReflectionModel(null)).toBe("");
    expect(providerForReflectionModel("" )).toBe("");
  });

  it("retains qualified IDs and qualifies bare slash-containing model IDs", () => {
    expect(qualifyReflectionModel("ollama", "ollama/qwen3:8b")).toBe("ollama/qwen3:8b");
    expect(qualifyReflectionModel("9router", "meta-llama/llama-3.3-70b"))
      .toBe("9router/meta-llama/llama-3.3-70b");
    expect(qualifyReflectionModel("9router", "9router/meta-llama/llama-3.3-70b"))
      .toBe("9router/meta-llama/llama-3.3-70b");
  });
});
