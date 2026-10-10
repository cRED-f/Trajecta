import {
  AlertCircle,
  Brain,
  Check,
  ChevronDown,
  Circle,
  LoaderCircle,
  Wrench,
} from "lucide-react";

import { memo, useEffect, useLayoutEffect, useRef, useState } from "react";

import type { StreamState, ToolActivity } from "../types/chat";

import { isAtScrollBottom } from "./chat-scroll-policy";

interface Props {
  stream: StreamState;
  /** Restored activity is always initially collapsed; live activity closes
   * automatically once the assistant starts answering or finishes the run. */
  variant?: "live" | "saved";
}

function toolState(tool: ToolActivity, running: boolean): "running" | "complete" | "error" | "unknown" {
  if (tool.status === "error") return "error";
  if (tool.result !== undefined || tool.status === "success") return "complete";
  return running ? "running" : "unknown";
}

function formatPayload(value: string): string {
  const text = value.trim();
  if (!text) return "";

  try {
    return JSON.stringify(JSON.parse(text), null, 2);
  } catch {
    return text;
  }
}

const ToolRow = memo(function ToolRow({ tool, running }: { tool: ToolActivity; running: boolean }) {
  const state = toolState(tool, running);
  const input = formatPayload(tool.args);
  const output = tool.result === undefined ? "" : formatPayload(tool.result);
  const hasDetails = Boolean(input || output);

  const summary = (
    <>
      <span className="agent-activity__tool-icon" aria-hidden="true">
        {state === "running" ? (
          <LoaderCircle size={13} className="agent-activity__spinner" />
        ) : state === "complete" ? (
          <Check size={13} />
        ) : state === "error" ? (
          <AlertCircle size={13} />
        ) : (
          <Circle size={11} />
        )}
      </span>
      <span className="agent-activity__tool-name">{tool.name || "Tool call"}</span>
      {tool.source && tool.source !== "main" && (
        <span className="agent-activity__tool-source">{tool.source}</span>
      )}
      <span className="agent-activity__tool-status">
        {state === "running" ? "Running" :
          state === "complete" ? "Done" :
          state === "error" ? "Failed" : "No result"}
      </span>
      {hasDetails && <ChevronDown className="agent-activity__tool-chevron" size={14} aria-hidden="true" />}
    </>
  );

  const className = `agent-activity__tool agent-activity__tool--${state}`;
  if (!hasDetails) {
    return <div className={className}><div className="agent-activity__tool-summary agent-activity__tool-summary--static">{summary}</div></div>;
  }

  return (
    <details className={className}>
      <summary className="agent-activity__tool-summary">{summary}</summary>
      <div className="agent-activity__tool-details">
        {input && (
          <div className="agent-activity__payload">
            <span className="agent-activity__payload-label">Input</span>
            <pre>{input}</pre>
          </div>
        )}
        {output && (
          <div className="agent-activity__payload">
            <span className="agent-activity__payload-label">Result</span>
            <pre>{output}</pre>
          </div>
        )}
      </div>
    </details>
  );
});

/** A single unobtrusive activity surface for reasoning and tool calls.
 * The final answer is rendered outside this component so it never gets buried
 * inside a growing tools / thinking card. */
export function StreamActivity({ stream, variant = "live" }: Props) {
  const live = variant === "live";
  const answering = stream.text.length > 0;
  const working = live && stream.running && !answering;
  const hasDetails = Boolean(stream.reasoning.trim() || stream.tools.length);
  const [expanded, setExpanded] = useState(working);
  const reasoningRef = useRef<HTMLDivElement>(null);
  const followReasoningRef = useRef(true);
  const lastTouchYRef = useRef<number | null>(null);

  // Each live run starts in follow mode. The reader can scroll up inside the
  // reasoning pane to pause, and returning to the bottom re-enables follow.
  useEffect(() => {
    followReasoningRef.current = true;
  }, [stream.runId]);

  // React commits each reasoning update before layout effects run, so
  // scrollTop can follow the latest token without smooth-scroll animations,
  // timers or modifying the position of the outer chat transcript.
  useLayoutEffect(() => {
    const el = reasoningRef.current;
    if (working && expanded && el && followReasoningRef.current) {
      el.scrollTop = el.scrollHeight;
    }
  }, [stream.reasoning, working, expanded]);

  // A new live run reopens the timeline even when the component is reused.
  // Transitioning into the answer (or stopping) closes it automatically.
  // Manual collapsing during one run is never overridden by new tokens.
  useEffect(() => {
    setExpanded(working);
  }, [working, stream.runId]);

  // Empty restored activity is not useful. It previously left an always
  // visible, misleading "Thinking" dropdown under finished responses.
  if (!live && !hasDetails) return null;
  if (live && !working && !hasDetails) return null;

  const toolCount = stream.tools.length;
  const errorCount = stream.tools.filter((tool) => tool.status === "error").length;
  const pendingTool = working
    ? [...stream.tools].reverse().find((tool) => toolState(tool, true) === "running")
    : undefined;
  const status = working
    ? pendingTool ? `Using ${pendingTool.name || "a tool"}` : "Working on your request"
    : "Activity";
  const summary = toolCount > 0
    ? `${toolCount} ${toolCount === 1 ? "tool" : "tools"}${live && stream.running ? "" : " used"}`
    : stream.reasoning.trim() ? "Reasoning available" : "";

  return (
    <section className={`agent-activity${working ? " agent-activity--working" : ""}`}
      aria-label="Assistant activity">
      <button
        className="agent-activity__toggle"
        type="button"
        aria-expanded={expanded}
        onClick={() => setExpanded((value) => !value)}
      >
        <span className="agent-activity__symbol" aria-hidden="true">
          {working ? (
            <LoaderCircle size={15} className="agent-activity__spinner" />
          ) : errorCount > 0 ? (
            <AlertCircle size={15} />
          ) : (
            <Wrench size={15} />
          )}
        </span>
        <span className="agent-activity__heading">{status}</span>
        {summary && <span className="agent-activity__summary">{summary}</span>}
        <ChevronDown size={14} className={`agent-activity__chevron${expanded ? " agent-activity__chevron--open" : ""}`}
          aria-hidden="true" />
      </button>

      {expanded && (
        <div className="agent-activity__body">
          {stream.reasoning.trim() && (
            <div className="agent-activity__reasoning">
              <div className="agent-activity__section-label">
                <Brain size={13} aria-hidden="true" /> Model reasoning
              </div>
              <div
                ref={reasoningRef}
                className="agent-activity__reasoning-text"
                tabIndex={0}
                role="region"
                aria-label="Live model reasoning"
                onScroll={(event) => {
                  // Scroll events caused by our own pin stay at the bottom.
                  // Any user scroll away suspends follow until they return.
                  followReasoningRef.current = isAtScrollBottom(event.currentTarget);
                }}
                onWheel={(event) => {
                  // Honor a small upward wheel gesture immediately, before
                  // the next token arrives and could undo the user's scroll.
                  if (event.deltaY < 0 &&
                    event.currentTarget.scrollHeight > event.currentTarget.clientHeight + 3) {
                    followReasoningRef.current = false;
                  }
                }}
                onKeyDown={(event) => {
                  if (["ArrowUp", "PageUp", "Home"].includes(event.key)) {
                    followReasoningRef.current = false;
                  }
                }}
                onTouchStart={(event) => {
                  lastTouchYRef.current = event.touches[0]?.clientY ?? null;
                }}
                onTouchMove={(event) => {
                  const y = event.touches[0]?.clientY;
                  if (y !== undefined && lastTouchYRef.current !== null &&
                      y > lastTouchYRef.current) {
                    followReasoningRef.current = false;
                  }
                  if (y !== undefined) lastTouchYRef.current = y;
                }}
                onTouchEnd={() => { lastTouchYRef.current = null; }}
              >{stream.reasoning}</div>
            </div>
          )}

          {toolCount > 0 && (
            <div className="agent-activity__tools" aria-label="Tool activity">
              {stream.tools.map((tool) => (
                <ToolRow key={tool.key} tool={tool} running={live && stream.running} />
              ))}
            </div>
          )}

          {!hasDetails && working && (
            <div className="agent-activity__waiting">
              Preparing a response
              <span className="thinking-dots" aria-hidden="true"><span /><span /><span /></span>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
