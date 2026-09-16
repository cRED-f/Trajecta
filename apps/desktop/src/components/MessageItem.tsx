import {
  Check,
  Copy,
  Pencil,
  RefreshCcw,
  RotateCcw,
} from "lucide-react";

import {
  useMemo,
  useState,
} from "react";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import {
  AttachmentChip,
} from "./AttachmentChip";

import { chatApi } from "../lib/api";

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