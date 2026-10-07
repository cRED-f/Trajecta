import {
  PanelLeftClose,
  PanelLeftOpen,
  RotateCcw,
} from "lucide-react";

interface Props {
  /** How many times this session has been resent. */
  resendCount?: number;

  sidebarOpen: boolean;

  onToggleSidebar(): void;
}

/**
 * Floating control bar over the conversation. The chat has no header
 * bar, so the sidebar toggle and the session's resend count live here
 * instead. Branch switching is left to the version switchers in the
 * transcript — raw branch ids like `resend:0f3a91c2` read as noise up
 * here.
 */
export function ChatFloatingBar({
  resendCount = 0,
  sidebarOpen,
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
