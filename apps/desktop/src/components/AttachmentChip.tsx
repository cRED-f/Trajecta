import {
  File,
  FileImage,
  FileText,
  X,
} from "lucide-react";

import { formatBytes } from "../lib/format";

import type {
  Attachment,
} from "../types/chat";

interface PendingProps {
  file: File;
  onRemove?: () => void;
}

export function PendingAttachmentChip({
  file,
  onRemove,
}: PendingProps) {
  const image =
    file.type.startsWith(
      "image/",
    );

  return (
    <div className="attachment-chip">
      {image ? (
        <FileImage size={15} />
      ) : (
        <FileText size={15} />
      )}

      <div className="attachment-chip__content">
        <span className="attachment-chip__name">
          {file.name}
        </span>

        <span className="attachment-chip__meta">
          {formatBytes(file.size)}
        </span>
      </div>

      {onRemove && (
        <button
          className="icon-button icon-button--tiny"
          onClick={onRemove}
          type="button"
          aria-label={`Remove ${file.name}`}
        >
          <X size={14} />
        </button>
      )}
    </div>
  );
}

interface ExistingProps {
  attachment: Attachment;
  onOpen?: () => void;
}

export function AttachmentChip({
  attachment,
  onOpen,
}: ExistingProps) {
  return (
    <button
      className="attachment-chip attachment-chip--button"
      type="button"
      onClick={onOpen}
    >
      {attachment.kind ===
      "image" ? (
        <FileImage size={15} />
      ) : attachment.kind ===
          "pdf" ||
        attachment.kind ===
          "docx" ? (
        <FileText size={15} />
      ) : (
        <File size={15} />
      )}

      <div className="attachment-chip__content">
        <span className="attachment-chip__name">
          {attachment.filename}
        </span>

        <span className="attachment-chip__meta">
          {formatBytes(
            attachment.size_bytes,
          )}
        </span>
      </div>
    </button>
  );
}