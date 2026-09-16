import { Brain, Check, LoaderCircle, Wrench } from "lucide-react";

import type { StreamState, ToolActivity } from "../types/chat";

interface Props {
  stream: StreamState;
}

function ToolStep({ tool, active }: { tool: ToolActivity; active: boolean }) {
  const complete = Boolean(tool.result) || tool.status === "success";

  return (
    <div className="run-timeline__item">
      <div className="run-timeline__rail">
        <div
          className={[
            "run-timeline__dot",
            complete
              ? "run-timeline__dot--complete"
              : active
                ? "run-timeline__dot--active"
                : "",
          ]
            .filter(Boolean)
            .join(" ")}
        >
          {complete ? (
            <Check size={11} />
          ) : active ? (
            <LoaderCircle className="run-timeline__spinner" size={12} />
          ) : (
            <Wrench size={11} />
          )}
        </div>

        <div className="run-timeline__line" />
      </div>

      <div className="run-timeline__content">
        <div className="run-timeline__title">Using {tool.name ?? "tool"}</div>

        {complete && <div className="run-timeline__meta">Completed</div>}
      </div>
    </div>
  );
}

export function StreamActivity({ stream }: Props) {
  const hasTools = stream.tools.length > 0;

  const answering = stream.text.length > 0;

  const thinking = stream.running && !hasTools && !answering;

  return (
    <div className="run-timeline">
      {/* Thinking */}
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
              <LoaderCircle className="run-timeline__spinner" size={12} />
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
        </div>
      </div>

      {/* Tools */}
      {stream.tools.map((tool, index) => {
        const active =
          stream.running &&
          !tool.result &&
          index === stream.tools.length - 1 &&
          !answering;

        return <ToolStep key={tool.key} tool={tool} active={active} />;
      })}

      {/* Answering */}
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
              {answering ? (
                <LoaderCircle className="run-timeline__spinner" size={12} />
              ) : null}
            </div>
          </div>

          <div className="run-timeline__content">
            <div className="run-timeline__title">Answering</div>
          </div>
        </div>
      )}
    </div>
  );
}
