import {
  Brain,
  Check,
  ChevronDown,
  Copy,
  Pencil,
  RefreshCcw,
  RotateCcw,
  ThumbsUp,
  ThumbsDown,
} from "lucide-react";

import {
  type ReactNode,
  useMemo,
  useState,
} from "react";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import {
  AttachmentChip,
} from "./AttachmentChip";

import { StreamActivity } from "./StreamActivity";

import { restoreActivityEvents } from "../stores/chat-store";

import { chatApi, learningApi } from "../lib/api";

import {
  relativeTime,
} from "../lib/format";

import type {
  Attachment,
  ChatMessage,
} from "../types/chat";

interface Props {
  message: ChatMessage;
  attachments: Attachment[];

  onEdit(
    message: ChatMessage,
  ): void;

  onResend(
    message: ChatMessage,
  ): void;

  onRegenerate(
    message: ChatMessage,
  ): void;
}

function nodeText(node: ReactNode): string {
  if (
    node === null ||
    node === undefined ||
    node === false ||
    node === true
  ) {
    return "";
  }

  if (
    typeof node === "string" ||
    typeof node === "number"
  ) {
    return String(node);
  }

  if (Array.isArray(node)) {
    return node
      .map((child) => nodeText(child))
      .join("");
  }

  const element = node as unknown as {
    props?: {
      children?: ReactNode;
    };
  };

  return nodeText(element.props?.children);
}

// react-markdown renders fenced blocks as `pre` wrapping `code`, so the
// language hint and the copy payload both live on the child.
function codeLanguage(
  children: ReactNode,
): string | null {
  const child = Array.isArray(children)
    ? children[0]
    : children;

  if (
    !child ||
    typeof child !== "object"
  ) {
    return null;
  }

  const element = child as unknown as {
    props?: {
      className?: string;
    };
  };

  const match =
    element.props?.className?.match(
      /language-([\w-]+)/,
    );

  return match?.[1] ?? null;
}

function CodeBlock({
  children,
}: {
  children?: ReactNode;
}) {
  const [copied, setCopied] =
    useState(false);
  const language =
    codeLanguage(children);

  async function copyCode() {
    await navigator.clipboard.writeText(
      nodeText(children),
    );

    setCopied(true);

    window.setTimeout(
      () => setCopied(false),
      1200,
    );
  }

  return (
    <div className="markdown-code">
      <div className="markdown-code__bar">
        <span className="markdown-code__label">
          {language ?? "code"}
        </span>

        <button
          className="icon-button icon-button--tiny"
          type="button"
          aria-label="Copy code"
          title={
            copied
              ? "Copied"
              : "Copy code"
          }
          onClick={copyCode}
        >
          {copied ? (
            <Check size={14} />
          ) : (
            <Copy size={14} />
          )}
        </button>
      </div>

      <pre>{children}</pre>
    </div>
  );
}

export function MessageItem({
  message,
  attachments,
  onEdit,
  onResend,
  onRegenerate,
}: Props) {
  const [copied, setCopied] =
    useState(false);

  const [feedback, setFeedback] = useState<"success" | "failure" | null>(null);
  const [feedbackError, setFeedbackError] = useState<string | null>(null);
  const [feedbackBusy, setFeedbackBusy] = useState(false);
  const trajectoryId = typeof message.metadata?.trajectory_id === "string"
    ? message.metadata.trajectory_id : null;

  async function rate(rating: "success" | "failure") {
    if (!trajectoryId || feedbackBusy || feedback) return;
    setFeedbackBusy(true);
    try {
      await learningApi.feedback(trajectoryId, rating);
      setFeedback(rating);
      setFeedbackError(null);
    } catch (cause) {
      setFeedbackError(cause instanceof Error ? cause.message : "Could not save feedback");
    } finally {
      setFeedbackBusy(false);
    }
  }


  const user =
    message.role === "user";

  const savedActivity = useMemo(
    () => restoreActivityEvents(message.metadata?.activity_events),
    [message.metadata?.activity_events],
  );

  const humanInteractions = Array.isArray(message.metadata?.human_interactions)
    ? message.metadata.human_interactions.filter(
        (item): item is { question: string; answer: string } =>
          item !== null && typeof item === "object" &&
          typeof item.question === "string" && typeof item.answer === "string",
      )
    : [];

  const attachmentUrls =
    useMemo(
      () =>
        attachments.map(
          (attachment) => ({
            attachment,

            url:
              chatApi.attachmentContentUrl(
                message.conversation_id,
                attachment.id,
              ),
          }),
        ),

      [
        attachments,
        message.conversation_id,
      ],
    );

  async function copy() {
    await navigator.clipboard.writeText(
      message.content,
    );

    setCopied(true);

    window.setTimeout(
      () =>
        setCopied(false),
      1200,
    );
  }

  return (
    <article
      className={`message ${
        user
          ? "message--user"
          : "message--assistant"
      } ${
        message.status === "pending"
          ? "message--pending"
          : ""
      }`}
    >
      <div className="message__meta">
        <span className="message__author">
          {user ? "You" : "Trajecta"}
        </span>

        <span className="message__time">
          {relativeTime(
            message.created_at,
          )}
        </span>
      </div>

      <div className="message__content">
        {!user && savedActivity && (
          <details className="thinking-disclosure">
            <summary className="thinking-disclosure__trigger">
              <Brain size={14} aria-hidden="true" />
              <span>Thinking</span>
              <ChevronDown
                size={14}
                className="thinking-disclosure__chevron"
                aria-hidden="true"
              />
            </summary>

            <div className="thinking-disclosure__body">
              {savedActivity.steps.length === 0 &&
              savedActivity.tools.length === 0 &&
              !savedActivity.reasoning ? (
                <p className="thinking-disclosure__empty">
                  No additional activity details recorded.
                </p>
              ) : (
                <StreamActivity stream={savedActivity} />
              )}
            </div>
          </details>
        )}

        {attachmentUrls.length >
          0 && (
          <div className="message__attachments">
            {attachmentUrls.map(
              ({
                attachment,
                url,
              }) => (
                <AttachmentChip
                  key={
                    attachment.id
                  }
                  attachment={
                    attachment
                  }
                  onOpen={() =>
                    window.open(
                      url,
                      "_blank",
                    )
                  }
                />
              ),
            )}
          </div>
        )}

        {user ? (
          <div className="user-message-text">
            {message.content}
          </div>
        ) : (
          <div className="markdown">
            <ReactMarkdown
              remarkPlugins={[
                remarkGfm,
              ]}
              components={{
                pre: ({ children }) => (
                  <CodeBlock>
                    {children}
                  </CodeBlock>
                ),
              }}
            >
              {message.content}
            </ReactMarkdown>
          </div>
        )}
        {!user && humanInteractions.length > 0 && (
          <div className="message-interactions">
            {humanInteractions.map(({ question, answer }, index) => (
              <div className="message-interactions__item" key={index}>
                <strong>{question}</strong>
                <p>Your answer: {answer}</p>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="message-actions">
        <button
          className="icon-button"
          type="button"
          aria-label="Copy message"
          title="Copy"
          onClick={copy}
        >
          {copied ? (
            <Check size={15} />
          ) : (
            <Copy size={15} />
          )}
        </button>

        {user && (
          <>
            <button
              className="icon-button"
              type="button"
              aria-label="Edit message"
              title="Edit"
              onClick={() =>
                onEdit(message)
              }
            >
              <Pencil size={15} />
            </button>

            <button
              className="icon-button"
              type="button"
              disabled
              aria-label="Resend message"
              title="Resend"
              onClick={() =>
                onResend(message)
              }
            >
              <RotateCcw
                size={15}
              />
            </button>
          </>
        )}

        {!user && (
          <>
          {trajectoryId && <>
            <button className="icon-button" type="button" aria-label="Helpful response"
              title={feedback === "success" ? "Saved as successful" : "Mark helpful"}
              disabled={feedbackBusy || feedback !== null}
              onClick={() => void rate("success")}>
              <ThumbsUp size={15} />
            </button>
            <button className="icon-button" type="button" aria-label="Unhelpful response"
              title={feedback === "failure" ? "Feedback recorded" : "Mark unhelpful"}
              disabled={feedbackBusy || feedback !== null}
              onClick={() => void rate("failure")}>
              <ThumbsDown size={15} />
            </button>
          </>}
          <button
            className="icon-button"
            type="button"
            aria-label="Regenerate response"
            title="Regenerate"
            onClick={() =>
              onRegenerate(
                message,
              )
            }
          >
            <RefreshCcw
              size={15}
            />
          </button>
          </>
        )}
      </div>
      {feedbackError && <p role="alert" className="settings-error-card">{feedbackError}</p>}
    </article>
  );
}
