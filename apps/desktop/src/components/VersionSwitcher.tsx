import {
  ChevronLeft,
  ChevronRight,
} from "lucide-react";

import type {
  ChatBranch,
} from "../types/chat";

interface Props {
  /** Every branch of this turn, the original first. */
  versions: ChatBranch[];

  /** Which of `versions` the conversation is currently showing. */
  activeIndex: number;

  disabled?: boolean;

  onSelect(
    branchId: string,
  ): void;
}

/** Turn the stored label (`resend:0f3a91c2`) into something readable. */
function versionLabel(
  branch: ChatBranch,
  index: number,
): string {
  // The first version in a group is the conversation as it was before
  // the fork, whatever branch it happens to live on now.
  if (index === 0) {
    return "Original";
  }

  const label = branch.label ?? "";

  if (label.startsWith("edit:")) {
    return "Edited";
  }

  if (
    label.startsWith("regenerate:")
  ) {
    return "Regenerated";
  }

  if (
    label.startsWith("resend:")
  ) {
    return "Resent";
  }

  return label || "Version";
}

/**
 * ChatGPT-style version switch for a turn that was forked. Sits above
 * the divergent message so a resend can be undone without hunting for
 * a branch picker elsewhere.
 */
export function VersionSwitcher({
  versions,
  activeIndex,
  disabled,
  onSelect,
}: Props) {
  const current =
    versions[activeIndex];

  if (!current) {
    return null;
  }

  return (
    <div
      className="version-switcher"
      role="group"
      aria-label="Message versions"
    >
      <button
        className="version-switcher__step"
        type="button"
        disabled={
          disabled ||
          activeIndex <= 0
        }
        aria-label="Show previous version"
        title="Previous version"
        onClick={() => {
          const previous = versions[activeIndex - 1];
          if (previous) onSelect(previous.id);
        }}
      >
        <ChevronLeft size={14} />
      </button>

      <span className="version-switcher__count">
        {activeIndex + 1} /{" "}
        {versions.length}
      </span>

      <button
        className="version-switcher__step"
        type="button"
        disabled={
          disabled ||
          activeIndex >=
            versions.length - 1
        }
        aria-label="Show next version"
        title="Next version"
        onClick={() => {
          const next = versions[activeIndex + 1];
          if (next) onSelect(next.id);
        }}
      >
        <ChevronRight size={14} />
      </button>

      <span className="version-switcher__label">
        {versionLabel(
          current,
          activeIndex,
        )}
      </span>
    </div>
  );
}
