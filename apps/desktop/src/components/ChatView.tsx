import {
  AlertCircle,
  Sparkles,
} from "lucide-react";

import {
  useEffect,
  useMemo,
  useRef,
} from "react";

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

export function ChatView() {
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

  const bottomRef =
    useRef<HTMLDivElement>(
      null,
    );

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

  useEffect(() => {
    bottomRef.current?.scrollIntoView(
      {
        behavior:
          running
            ? "instant"
            : "smooth",
        block: "end",
      },
    );
  }, [
    messages.length,
    stream?.text,
    stream?.tools.length,
    running,
  ]);

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

      <div className="chat-scroll">
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

          <div ref={bottomRef} />
        </div>
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