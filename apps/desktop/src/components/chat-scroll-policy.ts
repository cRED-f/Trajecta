import type { StreamState } from "../types/chat";

/** Streaming reasoning is followed *inside* its own activity viewport.
 * The chat transcript only re-pins on activity milestones, not every token. */
export function isLiveActivityGrowing(stream: StreamState | undefined): boolean {
  return Boolean(
    stream?.running &&
      !stream.text &&
      (stream.reasoning.length > 0 || stream.tools.length > 0),
  );
}

/** Final answers follow the bottom until the reader scrolls away. */
export function shouldPinChat(scrolledUp: boolean, activityGrowing: boolean): boolean {
  return !scrolledUp && !activityGrowing;
}

/** A small threshold avoids losing follow mode to subpixel rounding while
 * preserving the user's ability to pause by scrolling above the latest text. */
export function isAtScrollBottom(
  element: Pick<HTMLElement, "scrollHeight" | "scrollTop" | "clientHeight">,
): boolean {
  return element.scrollHeight - element.scrollTop - element.clientHeight <= 3;
}
