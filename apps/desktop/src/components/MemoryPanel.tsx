import {
  Check,
  Key,
  LoaderCircle,
  Pencil,
  Plus,
  Trash2,
  X,
} from "lucide-react";

import {
  useMemo,
  useState,
} from "react";

import type {
  SavedMemory,
} from "../types/chat";

interface Props {
  memories: SavedMemory[] | undefined;

  loading: boolean;

  error: string | null;

  onClose(): void;

  onSave(
    key: string,
    content: string,
  ): Promise<void>;

  onDelete(key: string): Promise<void>;
}

export function MemoryPanel({
  memories,
  loading,
  error,
  onClose,
  onSave,
  onDelete,
}: Props) {
  const [draft, setDraft] = useState({
    key: "",
    content: "",
  });

  const [saving, setSaving] =
    useState(false);

  // Which memory is being edited (its key, if any).
  const [editingKey, setEditingKey] =
    useState<string | null>(null);

  const [localError, setLocalError] =
    useState<string | null>(null);

  const sorted = useMemo(
    () => [...(memories ?? [])].sort(
      (a, b) =>
        b.updated_at.localeCompare(a.updated_at),
    ),
    [memories],
  );

  function startEditing(memory: SavedMemory) {
    if (editingKey === memory.key) {
      return;
    }

    setEditingKey(memory.key);

    setDraft({
      key: memory.key,
      content: memory.content,
    });
  }

  function resetForm() {
    setEditingKey(null);

    setDraft({
      key: "",
      content: "",
    });
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();

    const key = draft.key.trim();
    const content = draft.content.trim();

    if (!key) {
      setLocalError("Enter a memory key.");

      return;
    }

    if (!content) {
      setLocalError("Enter some content.");

      return;
    }

    setLocalError(null);

    setSaving(true);

    try {
      await onSave(key, content);

      setDraft({
        key: "",
        content: "",
      });

      setEditingKey(null);
    } catch (caught) {
      setLocalError(
        caught instanceof Error
          ? caught.message
          : "Could not save memory.",
      );
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="memory-panel" aria-label="Saved memory">
      <header className="memory-panel__header">
        <div>
          <h2>Saved memory</h2>

          <p>
            Facts Trajecta remembers
            across conversations.
          </p>
        </div>

        <button
          className="memory-panel__close"
          type="button"
          onClick={onClose}
          aria-label="Close memory panel"
        >
          <X size={16} />
        </button>
      </header>

      <div className="memory-panel__body">
        <form
          className="memory-form"
          onSubmit={(event) =>
            void submit(event)
          }
        >
          <span className="memory-form__title">
            {editingKey
              ? "Edit memory"
              : "Add memory"}
          </span>

          <label className="memory-form__field">
            <span>Key</span>

            <input
              type="text"
              value={draft.key}
              disabled={saving}
              placeholder="user-preference"
              onChange={(event) =>
                setDraft((current) => ({
                  ...current,
                  key: event.target.value,
                }))
              }
            />
          </label>

          <label className="memory-form__field">
            <span>Content</span>

            <textarea
              value={draft.content}
              disabled={saving}
              rows={3}
              placeholder="What should Trajecta remember?"
              onChange={(event) =>
                setDraft((current) => ({
                  ...current,
                  content: event.target.value,
                }))
              }
            />
          </label>

          <div className="memory-form__actions">
            {editingKey ? (
              <>
                <button
                  className="memory-btn memory-btn--ghost"
                  type="button"
                  disabled={saving}
                  onClick={resetForm}
                >
                  Cancel
                </button>

                <button
                  className="memory-btn memory-btn--primary"
                  type="submit"
                  disabled={saving}
                >
                  {saving ? (
                    <>
                      <LoaderCircle
                        className="memory-spinner"
                        size={14}
                      />
                      Saving
                    </>
                  ) : (
                    <>
                      <Check size={14} />
                      Save changes
                    </>
                  )}
                </button>
              </>
            ) : (
              <button
                className="memory-btn memory-btn--primary"
                type="submit"
                disabled={saving}
              >
                {saving ? (
                  <>
                    <LoaderCircle
                      className="memory-spinner"
                      size={14}
                    />
                    Saving
                  </>
                ) : (
                  <>
                    <Plus size={14} />
                    Add memory
                  </>
                )}
              </button>
            )}
          </div>

          {(localError || error) && (
            <div className="memory-form__error">
              {localError ?? error}
            </div>
          )}
        </form>

        <div className="memory-list">
          <span className="memory-list__label">
            Stored memories
          </span>

          {loading && (
            <div className="memory-list__empty">
              Loading…
            </div>
          )}

          {!loading &&
            sorted.length === 0 && (
              <div className="memory-list__empty">
                No memories yet.
              </div>
            )}

          {!loading &&
            sorted.map((memory) => (
              <article
                className="memory-row"
                key={memory.id}
              >
                <div className="memory-row__body">
                  <span className="memory-row__key">
                    <Key size={12} />
                    {memory.key}
                  </span>

                  <p>{memory.content}</p>
                </div>

                <div className="memory-row__actions">
                  <button
                    className="memory-row__action"
                    type="button"
                    aria-label={`Edit ${memory.key}`}
                    title="Edit"
                    onClick={() =>
                      startEditing(memory)
                    }
                  >
                    <Pencil size={13} />
                  </button>

                  <button
                    className="memory-row__action"
                    type="button"
                    aria-label={`Delete ${memory.key}`}
                    title="Delete"
                    onClick={() =>
                      void onDelete(memory.key)
                    }
                  >
                    <Trash2 size={13} />
                  </button>
                </div>
              </article>
            ))}
        </div>
      </div>
    </section>
  );
}