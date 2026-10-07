import { Sparkles } from "lucide-react";

import { useEffect, useState } from "react";

interface AskPosition {
  left: number;
  top: number;
  text: string;
}

/**
 * Floating "Ask Trajecta" pill shown next to a text selection inside a
 * chat message. Clicking it puts the selected text into the composer.
 *
 * Coordinates are relative to `.chat-main` (the positioning context).
 * The pill is hidden the moment anything scrolls, so it never has to
 * be re-anchored against a moving selection.
 */
export function AskSelection({
  onAsk,
}: {
  onAsk(text: string): void;
}) {
  const [position, setPosition] =
    useState<AskPosition | null>(null);

  useEffect(() => {
    function hide() {
      setPosition(null);
    }

    function evaluate() {
      const selection = window.getSelection();

      if (
        !selection ||
        selection.isCollapsed ||
        selection.rangeCount === 0
      ) {
        hide();

        return;
      }

      const text = selection
        .toString()
        .trim();

      if (!text) {
        hide();

        return;
      }

      const range =
        selection.getRangeAt(0);

      const container =
        range.commonAncestorContainer;

      const node =
        container.nodeType ===
        Node.TEXT_NODE
          ? container.parentElement
          : (container as Element);

      // Only message bubbles: the composer, sidebar, and welcome
      // screen have their own ways to take text.
      if (
        !node ||
        !node.closest(".message")
      ) {
        hide();

        return;
      }

      const rect =
        range.getBoundingClientRect();

      if (
        rect.width === 0 &&
        rect.height === 0
      ) {
        hide();

        return;
      }

      const hostElement =
        document.querySelector<HTMLElement>(
          ".chat-main",
        );

      if (!hostElement) {
        return;
      }

      const hostRect =
        hostElement.getBoundingClientRect();

      const rawLeft =
        rect.left +
        rect.width / 2 -
        hostRect.left;

      const above =
        rect.top - hostRect.top;

      const below =
        rect.bottom - hostRect.top;

      // Sit above the selection, flipping below when it hugs the
      // top of the chat area (e.g. first message under the header).
      const top =
        above >= 44
          ? above - 40
          : below + 8;

      setPosition({
        left: Math.min(
          Math.max(rawLeft, 80),
          hostRect.width - 80,
        ),
        top,
        text,
      });
    }

    // Capture phase: scroll events bubble from the chat container.
    document.addEventListener(
      "selectionchange",
      evaluate,
    );

    document.addEventListener(
      "scroll",
      hide,
      true,
    );

    return () => {
      document.removeEventListener(
        "selectionchange",
        evaluate,
      );

      document.removeEventListener(
        "scroll",
        hide,
        true,
      );
    };
  }, []);

  if (!position) {
    return null;
  }

  return (
    <button
      className="ask-selection"
      type="button"
      style={{
        left: `${position.left}px`,
        top: `${position.top}px`,
      }}
      // Keep the selection alive: mousedown would otherwise collapse
      // it before the click lands.
      onMouseDown={(event) =>
        event.preventDefault()
      }
      onClick={() => {
        onAsk(position.text);

        window
          .getSelection()
          ?.removeAllRanges();

        setPosition(null);
      }}
    >
      <Sparkles size={13} />
      <span>Ask Trajecta</span>
    </button>
  );
}
