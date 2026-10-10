import {
  AlertCircle,
} from "lucide-react";

import {
  Fragment,
  useEffect,
  useLayoutEffect,
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
  isLiveActivityGrowing,
  shouldPinChat,
} from "./chat-scroll-policy";

import {
  VersionSwitcher,
} from "./VersionSwitcher";

import {
  ApprovalModal,
} from "./ApprovalModal";

import {
  AskSelection,
} from "./AskSelection";

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

  const actions =
    useChatActions();

  const scrollRef =
    useRef<HTMLDivElement>(
      null,
    );

  // The transcript itself, so growth anywhere in it can re-pin the view.
  const columnRef =
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

  // A live activity panel has an independent, user-scrollable reasoning viewport.
  // Keep the transcript stable between activity milestones, not each token.
  const activityGrowing = isLiveActivityGrowing(stream);

  // A queued ResizeObserver callback can fire after an activity update.
  // Keep the latest pause state available even to that stale callback.
  const activityGrowingRef = useRef(activityGrowing);
  activityGrowingRef.current = activityGrowing;

const approval =
    approvalQuery.data ??
    null;

  // A conversation without a pinned folder falls back to the configured
  // default, which reads as "nothing selected" in the bar.
  const workspacePath =
    typeof detail?.metadata.workspace_path === "string"
      ? detail.metadata.workspace_path
      : null;

  const messages =
    detail?.messages ?? [];

  // Follow the final answer; growing reasoning is scrolled inside StreamActivity.
  // A manually scrolled-up transcript must never be pulled back to the bottom.
  useEffect(() => {
    if (shouldPinChat(scrolledUp, activityGrowing)) {
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
    activityGrowing,
  ]);

  // Reveal the activity panel when it first appears, and each newly started
  // tool, but not on every reasoning token. The fixed-height reasoning pane
  // then follows its OWN live text, without bouncing the full transcript.
  useLayoutEffect(() => {
    if (!activityGrowing || scrolledUp) return;
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [activityGrowing, stream?.tools.length]);

  // Reveal the new run once, before reasoning begins, without following
  // every subsequent activity update. The user can scroll freely afterward.
  useEffect(() => {
    if (!running) {
      return;
    }

    setScrolledUp(false);
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [running]);

  // A switched conversation starts at the top, so drop the stale
  // jump-to-latest affordance left over from the previous one.
  useEffect(() => {
    setScrolledUp(false);
  }, [activeId]);

  // The transcript ResizeObserver must not chase reasoning tokens.
  // The reasoning pane handles its own bottom-follow while activity streams.
  useEffect(() => {
    const el = scrollRef.current;

    if (!el || !shouldPinChat(scrolledUp, activityGrowing)) {
      return;
    }

    const observer = new ResizeObserver(() => {
      if (shouldPinChat(false, activityGrowingRef.current)) {
        el.scrollTop = el.scrollHeight;
      }
    });

    observer.observe(el);

    const column = columnRef.current;

    if (column) {
      observer.observe(column);
    }

    return () => observer.disconnect();
  }, [scrolledUp, activityGrowing]);

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
        workspacePath={
          workspacePath
        }
        workspaceDisabled={
          running ||
          Boolean(approval)
        }
        onSelectWorkspace={
          actions.selectWorkspace
        }
        onToggleSidebar={
          onToggleSidebar
        }
      />

      <div
        className={`chat-scroll${activityGrowing ? " chat-scroll--activity" : ""}`}
        ref={scrollRef}
      >
        <div className="conversation-column" ref={columnRef}>
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
                  variant="live"
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