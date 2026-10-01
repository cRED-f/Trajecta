import {
  AlertCircle,
  BookMarked,
  Brain,
  Cpu,
  Database,
  RefreshCw,
  Search,
  Trash2,
} from "lucide-react";

import { useState } from "react";

import {
  useEmbeddingActions,
  useEmbeddingCatalog,
  useMemoryActions,
  useMemoryCatalog,
} from "../../hooks/use-settings";

import { SettingsToggle } from "./SettingsToggle";

const MEMORY_TYPES = [
  {
    tier: "semantic",
    label: "Semantic",
    description: "Durable user facts, preferences and decisions.",
    icon: Brain,
  },
  {
    tier: "episodic",
    label: "Episodic",
    description: "Conversation traces and past task experiences.",
    icon: BookMarked,
  },
  {
    tier: "procedural",
    label: "Procedural",
    description: "Verified skills the agent has learned to perform.",
    icon: Database,
  },
] as const;

interface Props {
  enabled: boolean;
  backendOnline: boolean;
}

export function MemorySettings({ enabled, backendOnline }: Props) {
  const [search, setSearch] = useState("");

  const query = useMemoryCatalog(search, enabled && backendOnline);
  const actions = useMemoryActions();

  const embedding = useEmbeddingCatalog(enabled && backendOnline);
  const embeddingActions = useEmbeddingActions();

  const embeddingModels = embedding.data?.models ?? [];
  const selectedEmbedding = embedding.data?.selected_model ?? "";

  const automaticMemory = query.data?.settings.automatic_memory ?? true;
  const items = query.data?.items ?? [];

  return (
    <section>
      <div className="settings-page-header">
        <div>
          <h3>Memory</h3>
          <p>Browse durable semantic memories and control automatic memory.</p>
        </div>
      </div>

      {!backendOnline ? (
        <div className="settings-empty-state">
          <AlertCircle size={22} />

          <strong>Backend disconnected</strong>

          <span>Connect to the Trajecta backend to manage memory.</span>
        </div>
      ) : (
        <>
          <div className="settings-group-card">
            <div className="settings-row">
              <div className="settings-row__icon">
                <Brain size={16} />
              </div>

              <div className="settings-row__body">
                <strong>Automatic memory</strong>
                <span>
                  Save durable facts from conversations without being asked.
                </span>
              </div>

              <SettingsToggle
                checked={automaticMemory}
                disabled={actions.updatingAutomaticMemory}
                label="Automatic memory"
                onChange={(value) =>
                  void actions.setAutomaticMemory(value)
                }
              />
            </div>

            <div className="settings-row settings-row--embedding">
              <div className="settings-row__icon">
                <Cpu size={16} />
              </div>

              <div className="settings-row__body">
                <strong>Embedding model</strong>
                <span>
                  Ollama model used by Qdrant for semantic memory and document
                  retrieval.
                </span>

                {embedding.data?.provider === "placeholder" && (
                  <span className="embedding-warning">
                    The development placeholder embedding is currently active.
                  </span>
                )}
              </div>

              <div className="embedding-model-control">
                <select
                  aria-label="Embedding model"
                  value={selectedEmbedding}
                  disabled={
                    embedding.isLoading ||
                    embeddingActions.selectingModel ||
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
                    embedding.isFetching || embeddingActions.selectingModel
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

            {embedding.data?.ollama.reachable && selectedEmbedding && (
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

          <div className="settings-subsection">
            <h4 className="settings-subsection__heading">
              Memory types
            </h4>

            <div className="memory-type-grid">
              {MEMORY_TYPES.map(({ tier, label, description, icon: Icon }) => (
                <div className="memory-type-card" key={tier}>
                  <div className="memory-type-card__icon">
                    <Icon size={17} />
                  </div>

                  <div className="memory-type-card__body">
                    <strong>{label}</strong>
                    <span>{description}</span>
                  </div>
                </div>
              ))}
            </div>
          </div>

          <div className="settings-subsection">
            <div className="settings-subsection__heading-row">
              <h4 className="settings-subsection__heading">
                Semantic memories
              </h4>

              {query.data && (
                <span className="settings-subsection__count">
                  {query.data.counts.semantic} shown ·{" "}
                  {query.data.counts.procedural} skills
                </span>
              )}
            </div>

            <div className="mcp-search settings-search">
              <Search size={15} />

              <input
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="Search semantic memories..."
              />
            </div>

            {query.isLoading && (
              <div className="settings-loading">
                <RefreshCw className="settings-spin" size={17} />
                Loading memories…
              </div>
            )}

            {query.isError && (
              <div className="settings-error-card">
                <AlertCircle size={17} />

                <span>
                  {query.error instanceof Error
                    ? query.error.message
                    : "Failed to load memories."}
                </span>
              </div>
            )}

            {!query.isLoading &&
              !query.isError &&
              items.length === 0 && (
                <div className="settings-empty-state settings-empty-state--small">
                  <Brain size={20} />

                  <strong>No semantic memories</strong>

                  <span>
                    {search
                      ? "Nothing matches your search."
                      : "Memories the agent saves will appear here."}
                  </span>
                </div>
              )}

            {!query.isLoading && !query.isError && items.length > 0 && (
              <div className="memory-item-list">
                {items.map((memory) => (
                  <div className="memory-item" key={memory.id}>
                    <div className="memory-item__type">semantic</div>

                    <div className="memory-item__body">
                      <strong>{memory.key}</strong>
                      <span>{memory.content}</span>
                    </div>

                    <button
                      type="button"
                      className="settings-icon-button"
                      aria-label={`Forget ${memory.key}`}
                      disabled={actions.deletingMemory}
                      onClick={() => void actions.deleteMemory(memory.key)}
                    >
                      <Trash2 size={15} />
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
        </>
      )}
    </section>
  );
}