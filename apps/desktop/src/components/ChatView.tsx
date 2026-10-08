import {
  AlertCircle,
} from "lucide-react";

import {
  Fragment,
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
  ChatFloatingBar,
} from "./ChatFloatingBar";

import {
  MessageItem,
} from "./MessageItem";

import {
  StreamActivity,
} from "./StreamActivity";

import {
  VersionSwitcher,
} from "./VersionSwitcher";

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
  ChatBranch,
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

  const branches =
    branchQuery.data ?? [];

  // Resends are stored as branches labelled `resend:<id>`.
  const resendCount = branches.filter(
    (branch) =>
      branch.label?.startsWith(
        "resend:",
      ),
  ).length;

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
    approval?.id,
    scrolledUp,
  ]);

  // A switched conversation starts at the top, so drop the stale
  // jump-to-latest affordance left over from the previous one.
  useEffect(() => {
    setScrolledUp(false);
  }, [activeId]);

  // The composer sits under the transcript, so growing it (a long
  // draft, a quote, attachments) shrinks this viewport without firing a
  // scroll event. Re-pin to the newest content while the reader is
  // already at the bottom, or the tail would slip out of view mid-type.
  useEffect(() => {
    const el = scrollRef.current;

    if (!el || scrolledUp) {
      return;
    }

    const observer = new ResizeObserver(() => {
      el.scrollTop = el.scrollHeight;
    });

    observer.observe(el);

    return () => observer.disconnect();
  }, [scrolledUp]);

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

  // ChatGPT-style version switches: group every branch forked from the
  // same message, then park a switch above that turn in the transcript.
  // A resend therefore reads as "one more version of this message"
  // instead of a conversation that silently swapped out from under you.
  const versionSwitches = useMemo(() => {
    const byIndex = new Map<
      number,
      {
        versions: ChatBranch[];
        activeIndex: number;
      }
    >();

    const groups =
      new Map<
        string,
        {
          parent: string;
          fork: string;
          children: ChatBranch[];
        }
      >();

    for (const branch of branches) {
      if (
        !branch.parent_branch_id ||
        !branch.fork_message_id
      ) {
        continue;
      }

      const key = `${branch.parent_branch_id}:${branch.fork_message_id}`;

      let group = groups.get(key);

      if (!group) {
        group = {
          parent:
            branch.parent_branch_id,
          fork: branch.fork_message_id,
          children: [],
        };

        groups.set(key, group);
      }

      group.children.push(branch);
    }

    for (const group of groups.values()) {
      const parent = branches.find(
        (branch) =>
          branch.id === group.parent,
      );

      if (!parent) {
        continue;
      }

      const versions = [
        parent,
        ...group.children,
      ];

      const activeIndex =
        versions.findIndex(
          (branch) =>
            branch.id === branchId,
        );

      if (
        activeIndex < 0 ||
        versions.length < 2
      ) {
        continue;
      }

      // On the parent the original message is still there; on a fork
      // it was replaced by the revision that carries the same origin.
      let index = messages.findIndex(
        (message) =>
          message.id === group.fork,
      );

      if (index < 0) {
        index = messages.findIndex(
          (message) =>
            message.revision_of ===
            group.fork,
        );
      }

      if (
        index < 0 ||
        byIndex.has(index)
      ) {
        continue;
      }

      byIndex.set(index, {
        versions,
        activeIndex,
      });
    }

    return byIndex;
  }, [branches, messages, branchId]);

  return (
    <main className="chat-main">
      <ChatFloatingBar
        resendCount={resendCount}
        sidebarOpen={
          sidebarOpen
        }
        onToggleSidebar={
          onToggleSidebar
        }
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
            (message, index) => {
              const switcher =
                versionSwitches.get(
                  index,
                );

              return (
                <Fragment
                  key={message.id}
                >
                  {switcher && (
                    <VersionSwitcher
                      versions={
                        switcher.versions
                      }
                      activeIndex={
                        switcher.activeIndex
                      }
                      disabled={
                        running ||
                        Boolean(approval)
                      }
                      onSelect={(
                        id,
                      ) => {
                        void actions.activateBranch(
                          id,
                        );
                      }}
                    />
                  )}

                  <MessageItem
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
                </Fragment>
              );
            },
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

          {/* The permission pause renders inline as the newest message
              instead of covering the conversation in a modal. */}
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

          </div>
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

      <AskSelection
        onAsk={setAskQuote}
      />

      <Composer
        running={running}
        showJumpButton={scrolledUp}
        model={currentModel}
        models={
          modelQuery.data
        }
        modelDisabled={
          running ||
          Boolean(approval)
        }
        pendingApproval={Boolean(
          approval,
        )}
        onJumpToLatest={scrollToLatest}
        onModelChange={(
          model,
        ) => {
          void actions.selectModel(
            model,
          );
        }}
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