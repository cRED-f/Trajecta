/** Cold-start contracts. Installed Tauri owns process startup; React only waits
 * for readiness and restores data via the existing FastAPI endpoints. */
export type StartupStep =
  | "fastapi"
  | "bifrost"
  | "settings"
  | "conversations"
  | "learning"
  | "embedding";

export type StepState = "pending" | "running" | "ready" | "warning" | "failed";

export const startupSteps: { id: StartupStep; title: string }[] = [
  { id: "fastapi", title: "Starting FastAPI and database" },
  { id: "bifrost", title: "Connecting to Bifrost" },
  { id: "settings", title: "Restoring your settings and models" },
  { id: "conversations", title: "Restoring recent conversations" },
  { id: "learning", title: "Resuming background learning" },
  { id: "embedding", title: "Checking selected embedding model" },
];

export type StartupProgress = Record<StartupStep, StepState>;
export const initialStartupProgress = (): StartupProgress => ({
  fastapi: "pending", bifrost: "pending", settings: "pending",
  conversations: "pending", learning: "pending", embedding: "pending",
});

export interface DesktopStartupStatus {
  backend_state: "connected" | "starting" | "unresponsive" | "offline" | "failed";
  bifrost_reachable: boolean;
  backend_log_path: string | null;
  bifrost_error: string | null;
  error: string | null;
}

export interface WarmupResult {
  embedding: { state: "skipped" | "ready" | "unavailable"; model?: string; reason?: string };
  learning: { state: "skipped" | "ready" | "unavailable" | "configured"; model?: string; reason?: string };
}

export type StartupNotice = { id: string; message: string; kind: "warning" | "info" };

export function chooseOllamaNotice(
  embedding: { enabled: boolean; selected_model: string | null; ollama: { reachable: boolean } } | null,
  learningModel: string | null,
  warmup: WarmupResult | null,
): StartupNotice | null {
  const ollamaLearning = Boolean(learningModel?.startsWith("ollama/"));
  const embeddingEnabled = Boolean(embedding?.enabled && embedding.selected_model);
  if (!embeddingEnabled && !ollamaLearning) return null;
  if (embedding && !embedding.ollama.reachable) {
    return {
      id: "ollama-offline", kind: "warning",
      message: "Ollama is not reachable. Open Ollama to use your selected local models; your saved selections are preserved.",
    };
  }
  const unavailable = (embeddingEnabled && warmup?.embedding.state === "unavailable") ||
    (ollamaLearning && warmup?.learning.state === "unavailable");
  if (unavailable) {
    return {
      id: "ollama-model", kind: "warning",
      message: "The selected Ollama model could not be loaded. Open Ollama and verify that the model is installed.",
    };
  }
  return null;
}
