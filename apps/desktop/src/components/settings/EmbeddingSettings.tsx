import { AlertCircle, Cpu, RefreshCw } from "lucide-react";

import { useEffect, useState } from "react";

import type { ReactNode } from "react";

import {
  useEmbeddingActions,
  useEmbeddingCatalog,
} from "../../hooks/use-settings";

import { SettingsToggle } from "./SettingsToggle";

interface Props {
  enabled: boolean;
  backendOnline: boolean;
}

export function EmbeddingSettings({ enabled, backendOnline }: Props) {
  const embedding = useEmbeddingCatalog(enabled && backendOnline);
  const embeddingActions = useEmbeddingActions();

  // The switch flips immediately; the target is dropped once the catalog
  // reports the same position, and reverted if the PATCH fails.
  const [targetOn, setTargetOn] = useState<boolean | null>(null);

  const embeddingModels = embedding.data?.models ?? [];
  const selectedEmbedding = embedding.data?.selected_model ?? "";

  const switchOn = embedding.data?.enabled ?? false;
  const live = embedding.data?.provider === "ollama";
  const dimensions = embedding.data?.dimensions;
  const switchOnNow = targetOn ?? switchOn;

  useEffect(() => {
    if (targetOn !== null && embedding.data?.enabled === targetOn) {
      setTargetOn(null);
    }
  }, [embedding.data?.enabled, targetOn]);

  function toggleSwitch(value: boolean) {
    setTargetOn(value);

    void embeddingActions.setEnabled(value).catch(() => setTargetOn(null));
  }

  function stateNote(): ReactNode {
    if (!embedding.data) {
      return null;
    }

    if (!embedding.data.vector_store_enabled) {
      return (
        <span className="embedding-warning">
          The vector store is disabled in config, so embeddings are
          unavailable.
        </span>
      );
    }

    if (!switchOnNow) {
      return (
        <span className="embedding-warning">
          Off — using the built-in default embedder ({dimensions} dimensions),
          which hashes keywords instead of meaning.
        </span>
      );
    }

    if (!live) {
      return (
        <span className="embedding-warning">
          Local embedding is on, but no model is active yet — pick one below.
        </span>
      );
    }

    return null;
  }

  return (
    <section>
      <div className="settings-page-header">
        <div>
          <h3>Embedding</h3>
          <p>
            Ollama model used by Qdrant for semantic memory and document
            retrieval.
          </p>
        </div>
      </div>

      {!backendOnline ? (
        <div className="settings-empty-state">
          <AlertCircle size={22} />

          <strong>Backend disconnected</strong>

          <span>Connect to the Trajecta backend to manage embeddings.</span>
        </div>
      ) : (
        <>
          <div className="settings-group-card">
            <div className="settings-row">
              <div className="settings-row__icon">
                <Cpu size={16} />
              </div>

              <div className="settings-row__body">
                <strong>Embedding model</strong>
                <span>
                  Use a local Ollama model for semantic retrieval, or switch
                  off to fall back to the built-in default embedder.
                </span>

                {stateNote()}
              </div>

              <SettingsToggle
                checked={switchOnNow}
                disabled={
                  embedding.isLoading ||
                  embedding.isFetching ||
                  embeddingActions.togglingEnabled ||
                  embeddingActions.selectingModel ||
                  !embedding.data?.vector_store_enabled
                }
                label="Embedding model"
                onChange={toggleSwitch}
              />
            </div>

            <div className="settings-row settings-row--embedding embedding-model-row">
              <div className="embedding-model-control">
                <select
                  aria-label="Embedding model"
                  value={selectedEmbedding}
                  disabled={
                    !switchOnNow ||
                    embedding.isLoading ||
                    embeddingActions.selectingModel ||
                    embeddingActions.togglingEnabled ||
                    !embedding.data?.ollama.reachable ||
                    !embedding.data?.vector_store_enabled ||
                    embeddingModels.length === 0
                  }
                  onChange={(event) => {
                    const model = event.target.value;

                    if (model && model !== selectedEmbedding) {
                      void embeddingActions.selectModel(model);
                    }
                  }}
                >
                  {!selectedEmbedding && (
                    <option value="">Select local model…</option>
                  )}

                  {selectedEmbedding &&
                    !embeddingModels.some(
                      (model) => model.name === selectedEmbedding,
                    ) && (
                      <option value={selectedEmbedding}>
                        {selectedEmbedding}
                        {" · Unavailable"}
                      </option>
                    )}

                  {embeddingModels.map((model) => (
                    <option key={model.name} value={model.name}>
                      {model.name}

                      {model.name.startsWith("qwen3-embedding:0.6b")
                        ? " · Recommended"
                        : ""}
                    </option>
                  ))}
                </select>

                <button
                  type="button"
                  className="settings-icon-button"
                  aria-label="Refresh Ollama embedding models"
                  disabled={
                    embedding.isFetching ||
                    embeddingActions.selectingModel ||
                    embeddingActions.togglingEnabled
                  }
                  onClick={() => void embedding.refetch()}
                >
                  <RefreshCw
                    size={15}
                    className={embedding.isFetching ? "settings-spin" : undefined}
                  />
                </button>
              </div>
            </div>
          </div>

          <div className="embedding-model-status">
            {embedding.isLoading && (
              <span>
                <RefreshCw className="settings-spin" size={13} />
                Discovering local Ollama embedding models…
              </span>
            )}

            {embeddingActions.togglingEnabled && (
              <span>
                <RefreshCw className="settings-spin" size={13} />
                {targetOn
                  ? "Switching to the local model and rebuilding Qdrant indexes…"
                  : "Switching to the default embedder and rebuilding Qdrant indexes…"}
              </span>
            )}

            {embedding.data && !embedding.data.ollama.reachable && (
              <span className="embedding-model-status--error">
                <AlertCircle size={13} />
                Ollama is not reachable at {embedding.data.ollama.base_url}.
              </span>
            )}

            {embedding.data?.ollama.reachable &&
              embeddingModels.length === 0 && (
                <span>
                  No installed Ollama models advertise embedding support.
                  Install qwen3-embedding:0.6b, then refresh.
                </span>
              )}

            {embedding.data && !switchOn && !embeddingActions.togglingEnabled && (
              <span>
                Off · default embedder · {embedding.data.dimensions}
                {" dimensions"}
              </span>
            )}

            {embedding.data?.ollama.reachable && switchOn && live && (
              <span>
                Active: {selectedEmbedding}
                {" · "}
                {embedding.data.dimensions}
                {" dimensions"}
              </span>
            )}

            {embeddingActions.selectingModel && (
              <span>
                <RefreshCw className="settings-spin" size={13} />
                Switching to {embeddingActions.pendingModel}
                {" and rebuilding Qdrant indexes…"}
              </span>
            )}

            {embeddingActions.error instanceof Error && (
              <span className="embedding-model-status--error">
                <AlertCircle size={13} />
                {embeddingActions.error.message}
              </span>
            )}
          </div>
        </>
      )}
    </section>
  );
}
