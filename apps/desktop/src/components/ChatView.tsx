import {
  AlertCircle,
  Sparkles,
} from "lucide-react";

import {
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import {
  ArrowDown,
} from "lucide-react";

import {
  useBranches,
  useChatActions,
  useConversation,
  useModels,
} from "../hooks/use-chat";

import { useChatStore } from "../stores/chat-store";

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

  const messages =
    detail?.messages ?? [];

  // Keep pinned to the latest text while at the bottom; once the user
  // scrolls up, stop following and surface a jump-to-latest button.
  // Pin the container's scrollTop directly — scrollIntoView can land
  // short while the message is still streaming and re-laying out.
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
    setScrolledUp(false);

    const el = scrollRef.current;

    if (el) {
      el.scrollTop = el.scrollHeight;
    }
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
        disabled={running}
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
            <div className="welcome">
              <Sparkles
                size={18}
              />

              <h1>
                How can I help?
              </h1>

              <p>
                Ask Trajecta to
                inspect files,
                research, write,
                debug, or use its
                tools.
              </p>
            </div>
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

              {running &&
                !assistantStream &&
                !stream?.tools.length && (
                  <div className="thinking">
                    Working
                    <span>.</span>
                    <span>.</span>
                    <span>.</span>
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