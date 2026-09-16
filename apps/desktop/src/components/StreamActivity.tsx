import {
  AlertCircle,
  Brain,
  Check,
  LoaderCircle,
  SquareTerminal,
  Wrench,
} from "lucide-react";

import type { StreamState, ToolActivity } from "../types/chat";

interface Props {
  stream: StreamState;
}

function formatPayload(value: string | undefined): string {
  const raw = value?.trim();

  if (!raw) {
    return "No data";
  }

  try {
    return JSON.stringify(JSON.parse(raw), null, 2);
  } catch {
    return raw;
  }
}

function ToolStep({ tool, active }: { tool: ToolActivity; active: boolean }) {
  const complete = Boolean(tool.result) || tool.status === "success";

  const failed = tool.status === "error";

  const input = formatPayload(tool.args);

  const result = formatPayload(tool.result);

  return (
    <div className="run-timeline__item">
      <div className="run-timeline__rail">
        <div
          className={[
            "run-timeline__dot",

            failed
              ? "run-timeline__dot--error"
              : complete
                ? "run-timeline__dot--complete"
                : active
                  ? "run-timeline__dot--active"
                  : "",
          ]
            .filter(Boolean)
            .join(" ")}
        >
          {failed ? (
            <AlertCircle size={11} />
          ) : complete ? (
            <Check size={11} />
          ) : active ? (
            <LoaderCircle size={12} className="run-timeline__spinner" />
          ) : (
            <Wrench size={11} />
          )}
        </div>

        <div className="run-timeline__line" />
      </div>

      <div className="run-timeline__content">
        <div className="run-timeline__title-row">
          <div className="run-timeline__title">
            <Wrench size={13} />
            Using {tool.name ?? "tool"}
          </div>

          <span
            className={[
              "run-timeline__status",

              failed
                ? "run-timeline__status--error"
                : complete
                  ? "run-timeline__status--complete"
                  : "run-timeline__status--active",
            ].join(" ")}
          >
            {failed ? "Failed" : complete ? "Completed" : "Running"}
          </span>
        </div>

        {tool.source && tool.source !== "main" && (
          <div className="run-timeline__source">{tool.source}</div>
        )}

        <div className="run-timeline__tool-grid">
          <section className="run-timeline__payload">
            <div className="run-timeline__payload-label">
              <SquareTerminal size={12} />
              Input
            </div>

            <pre>{input}</pre>
          </section>

          {(tool.result !== undefined || failed) && (
            <section className="run-timeline__payload">
              <div className="run-timeline__payload-label">
                <Check size={12} />
                Result
              </div>

              <pre>{result}</pre>
            </section>
          )}
        </div>
      </div>
    </div>
  );
}

export function StreamActivity({ stream }: Props) {
  const hasTools = stream.tools.length > 0;

  const answering = stream.text.length > 0;

  const thinking = stream.running && !hasTools && !answering;

  return (
    <div className="run-timeline" aria-label="Agent activity">
      <div className="run-timeline__item">
        <div className="run-timeline__rail">
          <div
            className={[
              "run-timeline__dot",

              thinking
                ? "run-timeline__dot--active"
                : "run-timeline__dot--complete",
            ].join(" ")}
          >
            {thinking ? (
              <LoaderCircle size={12} className="run-timeline__spinner" />
            ) : (
              <Check size={11} />
            )}
          </div>

          <div className="run-timeline__line" />
        </div>

        <div className="run-timeline__content">
          <div className="run-timeline__title">
            <Brain size={14} />
            Thinking
          </div>

          {stream.steps.length > 0 && (
            <div className="run-timeline__meta">
              Preparing context and deciding the next action
            </div>
          )}
        </div>
      </div>

      {stream.tools.map((tool, index) => {
        const active =
          stream.running &&
          !tool.result &&
          tool.status !== "error" &&
          index === stream.tools.length - 1 &&
          !answering;

        return <ToolStep key={tool.key} tool={tool} active={active} />;
      })}

      {(answering || (stream.running && hasTools)) && (
        <div className="run-timeline__item run-timeline__item--last">
          <div className="run-timeline__rail">
            <div
              className={[
                "run-timeline__dot",

                answering ? "run-timeline__dot--active" : "",
              ]
                .filter(Boolean)
                .join(" ")}
            >
              {answering && (
                <LoaderCircle size={12} className="run-timeline__spinner" />
              )}
            </div>
          </div>

          <div className="run-timeline__content">
            <div className="run-timeline__title">Answering</div>

            <div className="run-timeline__meta">
              Streaming the final response
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
