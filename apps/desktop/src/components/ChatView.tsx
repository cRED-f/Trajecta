import {
  AlertCircle,
  ArrowDown,
} from "lucide-react";

import {
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import {
  useBranches,
  useChatActions,
  useConversation,
  useModels,
  usePendingApproval,
  useSavedMemories,
} from "../hooks/use-chat";

import { useChatStore } from "../stores/chat-store";

import {
  ChatWelcome,
} from "./ChatWelcome";

import {
  Composer,
} from "./Composer";

import {
  ChatHeader,
} from "./ChatHeader";

import {
  MessageItem,
} from "./MessageItem";

import {
  StreamActivity,
} from "./StreamActivity";

import {
  ApprovalModal,
} from "./ApprovalModal";

import {
  AskSelection,
} from "./AskSelection";

import {
  MemoryPanel,
} from "./MemoryPanel";

import type {
  Attachment,
  ChatMessage,
} from "../types/chat";

function attachmentsFor(
  message: ChatMessage,
  attachments: Attachment[],
) {
  return attachments.filter(
    (attachment) =>
      attachment.message_id ===
      message.id,
  );
}

interface ChatViewProps {
  sidebarOpen: boolean;

  onToggleSidebar(): void;
}

export function ChatView({
  sidebarOpen,
  onToggleSidebar,
}: ChatViewProps) {
  const activeId =
    useChatStore(
      (state) =>
        state.activeConversationId,
    );

  const startEdit =
    useChatStore(
      (state) =>
        state.startEdit,
    );

  const setDraft =
    useChatStore(
      (state) =>
        state.setDraft,
    );

  const setAskQuote =
    useChatStore(
      (state) =>
        state.setAskQuote,
    );

  const newModel =
    useChatStore(
      (state) =>
        state.newConversationModel,
    );

  const stream = useChatStore(
    (state) =>
      activeId
        ? state.streams[
            activeId
          ]
        : undefined,
  );

  const conversationQuery =
    useConversation(
      activeId,
    );

  const branchQuery =
    useBranches(
      activeId,
    );

  const modelQuery =
    useModels();

  const approvalQuery =
    usePendingApproval(
      activeId,
    );

  const memoryQuery =
    useSavedMemories();

  const memoryPanelOpen =
    useChatStore(
      (state) =>
        state.memoryPanelOpen,
    );

  const setMemoryPanelOpen =
    useChatStore(
      (state) =>
        state.setMemoryPanelOpen,
    );

  const actions =
    useChatActions();

  const scrollRef =
    useRef<HTMLDivElement>(
      null,
    );

  const [scrolledUp, setScrolledUp] =
    useState(false);

  const detail =
    conversationQuery.data;

  const currentModel =
    detail?.model ??
    newModel ??
    modelQuery.data
      ?.default_model ??
    "openai/gpt-4o-mini";

  const running =
    stream?.running ??
    false;

const approval =
    approvalQuery.data ??
    null;

  const messages =
    detail?.messages ?? [];

  // Keep pinned to the latest text while at the bottom; once the user
  // scrolls up, stop following and surface a jump-to-latest button.
  // Pin the container's scrollTop directly — scrollIntoView can land
  // short while the message is still streaming and re-laying out.
  // While scrolledUp is true (including during the button's smooth
  // scroll) this stays out of the way so the animation can finish.
  useEffect(() => {
    if (!scrolledUp) {
      const el = scrollRef.current;

      if (el) {
        el.scrollTop = el.scrollHeight;
      }
    }
  }, [
    messages.length,
    stream?.text,
    stream?.tools.length,
    running,
    scrolledUp,
  ]);

  // A switched conversation starts at the top, so drop the stale
  // jump-to-latest affordance left over from the previous one.
  useEffect(() => {
    setScrolledUp(false);
  }, [activeId]);

  useEffect(() => {
    const el = scrollRef.current;

    if (!el) {
      return;
    }

    function onScroll() {
      if (!el) {
        return;
      }

      const { scrollTop, scrollHeight, clientHeight } = el;

      const atBottom =
        scrollHeight -
          scrollTop -
          clientHeight <
        80;

      setScrolledUp(!atBottom);
    }

    el.addEventListener(
      "scroll",
      onScroll,
      { passive: true },
    );

    return () =>
      el.removeEventListener(
        "scroll",
        onScroll,
      );
  }, []);

  function scrollToLatest() {
    const el = scrollRef.current;

    if (!el) {
      return;
    }

    // Animate the jump instead of snapping. `scrolledUp` is deliberately
    // left alone until the scroll handler sees the bottom: flipping it
    // here would fire the pinning effect and cut the animation short.
    el.scrollTo({
      top: el.scrollHeight,
      behavior: "smooth",
    });
  }

  const streamVisible =
    Boolean(
      stream &&
        (stream.running ||
          stream.text ||
          stream.error),
    );

  const empty =
    messages.length === 0 &&
    !streamVisible;

  const branchId =
    detail?.active_branch_id ??
    detail?.branch?.id ??
    null;

  const assistantStream =
    useMemo(
      () =>
        stream?.text ?? "",
      [stream?.text],
    );

  return (
    <main className="chat-main">
      <ChatHeader
        model={currentModel}
        models={
          modelQuery.data
        }
        branches={
          branchQuery.data
        }
        activeBranchId={
          branchId
        }
        disabled={
          running ||
          Boolean(approval)
        }
        sidebarOpen={
          sidebarOpen
        }
        onToggleSidebar={
          onToggleSidebar
        }
        onModelChange={(
          model,
        ) => {
          void actions.selectModel(
            model,
          );
        }}
        onBranchChange={(
          branch,
        ) => {
          void actions.activateBranch(
            branch,
          );
        }}
      />

      <div className="chat-scroll" ref={scrollRef}>
        <div className="conversation-column">
          {empty && (
            <ChatWelcome
              disabled={running}
              onSelect={setDraft}
            />
          )}

          {messages.map(
            (message) => (
              <MessageItem
                key={
                  message.id
                }
                message={
                  message
                }
                attachments={attachmentsFor(
                  message,
                  detail?.attachments ??
                    [],
                )}
                onEdit={
                  startEdit
                }
                onResend={(
                  value,
                ) =>
                  void actions.resend(
                    value,
                  )
                }
                onRegenerate={(
                  value,
                ) =>
                  void actions.regenerate(
                    value,
                  )
                }
              />
            ),
          )}

          {streamVisible && (
            <article className="message message--assistant message--streaming">
              <div className="message__meta">
                <span className="message__author">
                  Trajecta
                </span>

                <span className="message__time">
                  now
                </span>
              </div>

              {stream && (
                <StreamActivity
                  stream={stream}
                />
              )}

              {assistantStream && (
                <div className="markdown markdown--streaming">
                  {assistantStream}
                  {running && (
                    <span className="stream-cursor" />
                  )}
                </div>
              )}

              {stream?.warnings.map((warning, index) => (
                <div className="guardrail-warning" key={`${warning}-${index}`}>
                  <AlertCircle size={15} />
                  <span>{warning}</span>
                </div>
              ))}

              {stream?.error && (
                <div className="run-error">
                  <AlertCircle
                    size={15}
                  />

                  <span>
                    {stream.error}
                  </span>
                </div>
              )}

            </article>
          )}

          </div>

        {scrolledUp && (
          <button
            className="scroll-to-latest"
            type="button"
            onClick={scrollToLatest}
            aria-label="Jump to latest"
          >
            <ArrowDown size={16} />
          </button>
        )}
      </div>

      {memoryPanelOpen && (
        <MemoryPanel
          memories={memoryQuery.data}
          loading={memoryQuery.isLoading}
          error={
            memoryQuery.error instanceof Error
              ? memoryQuery.error.message
              : null
          }
          onClose={() =>
            setMemoryPanelOpen(false)
          }
          onSave={actions.saveMemory}
          onDelete={actions.deleteMemory}
        />
      )}

      <ApprovalModal
        approval={approval}
        submitting={running && Boolean(approval)}
        error={
          approvalQuery.error instanceof Error
            ? approvalQuery.error.message
            : null
        }
        onSubmit={async (decisions) => {
          await actions.resumeApproval(decisions);
        }}
      />

      <AskSelection
        onAsk={setAskQuote}
      />

      <Composer
        running={running}
        onSend={
          actions.send
        }
        onStop={
          actions.cancel
        }
      />
    </main>
  );
}