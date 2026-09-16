import {
  ChevronDown,
  Wrench,
} from "lucide-react";

import type {
  StreamState,
} from "../types/chat";

interface Props {
  stream: StreamState;
}

export function StreamActivity({
  stream,
}: Props) {
  const hasActivity =
    stream.tools.length > 0 ||
    stream.steps.length > 0;

  if (!hasActivity) {
    return null;
  }

  return (
    <details
      className="stream-activity"
      open={!stream.text}
    >
      <summary>
        <div className="stream-activity__summary">
          <Wrench size={14} />

          <span>
            {stream.tools.length >
            0
              ? `${stream.tools.length} tool ${
                  stream.tools.length ===
                  1
                    ? "call"
                    : "calls"
                }`
              : "Working"}
          </span>
        </div>

        <ChevronDown
          className="stream-activity__chevron"
          size={14}
        />
      </summary>

      <div className="stream-activity__body">
        {stream.steps.map(
          (step, index) => (
            <div
              className="stream-step"
              key={`${step}-${index}`}
            >
              {step}
            </div>
          ),
        )}

        {stream.tools.map(
          (tool) => (
            <div
              className="tool-call"
              key={tool.key}
            >
              <div className="tool-call__header">
                <span>
                  {tool.name ??
                    "Tool"}
                </span>

                {tool.status && (
                  <span className="muted">
                    {tool.status}
                  </span>
                )}
              </div>

              {tool.args && (
                <pre>
                  {tool.args}
                </pre>
              )}

              {tool.result && (
                <pre className="tool-call__result">
                  {tool.result}
                </pre>
              )}
            </div>
          ),
        )}
      </div>
    </details>
  );
}