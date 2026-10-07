import {
  Check,
  Copy,
  Pencil,
  RefreshCcw,
  RotateCcw,
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

import { chatApi } from "../lib/api";

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

  const user =
    message.role === "user";

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
        )}
      </div>
    </article>
  );
}
