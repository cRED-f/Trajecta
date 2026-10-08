import {
  PanelLeftClose,
  PanelLeftOpen,
  RotateCcw,
} from "lucide-react";

import { WorkspaceSelector } from "./WorkspaceSelector";

interface Props {
  /** How many times this session has been resent. */
  resendCount?: number;

  sidebarOpen: boolean;

  /** Host folder this conversation is pinned to; null means the default. */
  workspacePath: string | null;

  /** A run or a pending approval owns the conversation. */
  workspaceDisabled: boolean;

  onSelectWorkspace(path: string): Promise<void> | void;

  onToggleSidebar(): void;
}

/**
 * Floating control bar over the conversation. The chat has no header
 * bar, so the sidebar toggle, the session's resend count, and the
 * workspace folder live here instead. Branch switching is left to the
 * version switchers in the transcript — raw branch ids like
 * `resend:0f3a91c2` read as noise up here.
 *
 * The workspace button stays mounted while the stream runs, so the
 * folder Trajecta is working in remains visible mid-run; only the
 * button itself is disabled until the run settles.
 */
export function ChatFloatingBar({
  resendCount = 0,
  sidebarOpen,
  workspacePath,
  workspaceDisabled,
  onSelectWorkspace,
  onToggleSidebar,
}: Props) {
  return (
    <div className="chat-floating-bar">
      <button
        className="icon-button"
        type="button"
        onClick={
          onToggleSidebar
        }
        aria-label={
          sidebarOpen
            ? "Hide sidebar"
            : "Show sidebar"
        }
        title={
          sidebarOpen
            ? "Hide sidebar"
            : "Show sidebar"
        }
      >
        {sidebarOpen ? (
          <PanelLeftClose
            size={17}
          />
        ) : (
          <PanelLeftOpen
            size={17}
          />
        )}
      </button>

      <div className="chat-floating-bar__divider" />

      <WorkspaceSelector
        workspacePath={
          workspacePath
        }
        disabled={
          workspaceDisabled
        }
        onSelect={
          onSelectWorkspace
        }
      />

      {resendCount > 0 && (
        <>
          <div className="chat-floating-bar__divider" />

          <span
            className="chat-floating-bar__count"
            title={`${resendCount} ${
              resendCount === 1
                ? "resend"
                : "resends"
            } in this session`}
          >
            <RotateCcw size={13} />
            {resendCount}
          </span>
        </>
      )}
    </div>
  );
}
