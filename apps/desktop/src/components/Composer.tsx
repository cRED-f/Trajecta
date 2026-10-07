import {
  ArrowUp,
  Paperclip,
  Sparkles,
  Square,
  X,
} from "lucide-react";

import {
  type ClipboardEvent,
  type DragEvent,
  type KeyboardEvent,
  useEffect,
  useRef,
  useState,
} from "react";

import {
  PendingAttachmentChip,
} from "./AttachmentChip";

import { useChatStore } from "../stores/chat-store";

interface Props {
  running: boolean;

  onSend(): Promise<void> | void;

  onStop(): Promise<void> | void;
}

export function Composer({
  running,
  onSend,
  onStop,
}: Props) {
  const textarea =
    useRef<HTMLTextAreaElement>(
      null,
    );

  const input =
    useRef<HTMLInputElement>(
      null,
    );

  const [dragging, setDragging] =
    useState(false);

  const draft = useChatStore(
    (state) => state.draft,
  );

  const setDraft =
    useChatStore(
      (state) =>
        state.setDraft,
    );

  const files = useChatStore(
    (state) =>
      state.pendingFiles,
  );

  const askQuote = useChatStore(
    (state) =>
      state.askQuote,
  );

  const setAskQuote = useChatStore(
    (state) =>
      state.setAskQuote,
  );

  const addFiles =
    useChatStore(
      (state) =>
        state.addFiles,
    );

  const removeFile =
    useChatStore(
      (state) =>
        state.removeFile,
    );

  const composeMode =
    useChatStore(
      (state) =>
        state.composeMode,
    );

  const cancelEdit =
    useChatStore(
      (state) =>
        state.cancelEdit,
    );

  useEffect(() => {
    const element =
      textarea.current;

    if (!element) {
      return;
    }

    element.style.height =
      "0px";

    element.style.height =
      `${Math.min(
        element.scrollHeight,
        220,
      )}px`;
  }, [draft]);

  useEffect(() => {
    textarea.current?.focus();
  }, [
    composeMode.kind,
  ]);

  // The welcome cards, message edits, and resends fill the draft from
  // outside the textarea, so pull focus back when that happens.
  useEffect(() => {
    const element =
      textarea.current;

    if (
      element &&
      draft.length > 0 &&
      document.activeElement !==
        element
    ) {
      element.focus();
    }
  }, [draft]);

  // "Ask Trajecta" hands the selection to the composer as a quote box;
  // focus the input so the question can be typed right away.
  useEffect(() => {
    if (askQuote) {
      textarea.current?.focus();
    }
  }, [askQuote]);

  function acceptFiles(
    values: FileList | File[],
  ) {
    addFiles(
      Array.from(values),
    );
  }

  function onPaste(
    event: ClipboardEvent<HTMLTextAreaElement>,
  ) {
    const clipboardFiles =
      Array.from(
        event.clipboardData.files,
      );

    if (
      clipboardFiles.length >
      0
    ) {
      acceptFiles(
        clipboardFiles,
      );
    }
  }

  function onDrop(
    event: DragEvent,
  ) {
    event.preventDefault();

    setDragging(false);

    if (
      event.dataTransfer.files
        .length > 0
    ) {
      acceptFiles(
        event.dataTransfer.files,
      );
    }
  }

  function onKeyDown(
    event: KeyboardEvent<HTMLTextAreaElement>,
  ) {
    if (
      event.key === "Enter" &&
      !event.shiftKey &&
      !event.nativeEvent
        .isComposing
    ) {
      event.preventDefault();

      if (!running) {
        void onSend();
      }
    }
  }

  const canSend =
    draft.trim().length > 0 ||
    files.length > 0;

  return (
    <div className="composer-dock">
      <div
        className={`composer ${
          dragging
            ? "composer--dragging"
            : ""
        }`}
        onDragEnter={(
          event,
        ) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragOver={(event) =>
          event.preventDefault()
        }
        onDragLeave={() =>
          setDragging(false)
        }
        onDrop={onDrop}
      >
        {composeMode.kind ===
          "edit" && (
          <div className="composer-mode">
            <span>
              Editing message
            </span>

            <button
              className="icon-button icon-button--tiny"
              type="button"
              onClick={
                cancelEdit
              }
              aria-label="Cancel edit"
            >
              <X size={14} />
            </button>
          </div>
        )}

        {files.length > 0 && (
          <div className="pending-attachments">
            {files.map(
              (
                file,
                index,
              ) => (
                <PendingAttachmentChip
                  key={`${file.name}-${file.size}-${index}`}
                  file={file}
                  onRemove={() =>
                    removeFile(
                      index,
                    )
                  }
                />
              ),
            )}
          </div>
        )}

        {askQuote &&
          composeMode.kind !== "edit" && (
            <div className="composer-quote">
              <div className="composer-quote__label">
                <Sparkles size={13} />
                <span>
                  Selected text
                </span>
              </div>

              <p className="composer-quote__text">
                {askQuote}
              </p>

              <button
                className="icon-button icon-button--tiny composer-quote__remove"
                type="button"
                onClick={() =>
                  setAskQuote(null)
                }
                aria-label="Remove selected text"
                title="Remove"
              >
                <X size={14} />
              </button>
            </div>
          )}

        <textarea
          ref={textarea}
          className="composer-input"
          placeholder={
            composeMode.kind ===
            "edit"
              ? "Edit your message"
              : "Message Trajecta"
          }
          value={draft}
          rows={1}
          disabled={running}
          onChange={(event) =>
            setDraft(
              event.target.value,
            )
          }
          onKeyDown={
            onKeyDown
          }
          onPaste={onPaste}
        />

        <div className="composer-actions">
          <input
            ref={input}
            hidden
            multiple
            type="file"
            accept="image/*,.pdf,.docx,.txt,.md,.markdown,.csv,.json,.yaml,.yml,.py,.js,.ts,.tsx,.jsx,.html,.css,.sql,.go,.rs,.java,.cpp,.c,.h"
            onChange={(event) => {
              if (
                event.target
                  .files
              ) {
                acceptFiles(
                  event.target
                    .files,
                );

                event.target.value =
                  "";
              }
            }}
          />

          <button
            className="icon-button"
            type="button"
            disabled={running}
            onClick={() =>
              input.current?.click()
            }
            aria-label="Attach files"
            title="Attach files"
          >
            <Paperclip size={17} />
          </button>

          <span className="composer-hint">
            Shift + Enter for
            newline
          </span>

          <div className="composer-spacer" />

          {running ? (
            <button
              className="send-button"
              type="button"
              aria-label="Stop"
              title="Stop"
              onClick={() =>
                void onStop()
              }
            >
              <Square
                size={13}
                fill="currentColor"
              />
            </button>
          ) : (
            <button
              className="send-button"
              type="button"
              aria-label="Send"
              title="Send"
              disabled={!canSend}
              onClick={() =>
                void onSend()
              }
            >
              <ArrowUp size={17} />
            </button>
          )}
        </div>
      </div>

      <div className="composer-disclaimer">
        Trajecta can use tools
        and modify your local
        workspace. Review sensitive
        actions.
      </div>
    </div>
  );
}