import { GitBranch } from "lucide-react";

import { percent } from "../../lib/format";

import type { SkillExperiment } from "../../types/settings";

interface Props {
  experiments: SkillExperiment[];

  /** Rendered when there is nothing to show. */
  emptyNote?: string;
}

/** Strategy, status and per-arm metrics for a set of experiments. */
export function ExperimentList({ experiments, emptyNote }: Props) {
  if (experiments.length === 0) {
    return emptyNote ? (
      <p className="skill-analytics-note">{emptyNote}</p>
    ) : null;
  }

  return (
    <div className="skill-list">
      {experiments.map((experiment) => (
        <div className="skill-row" key={experiment.id}>
          <div className="skill-row__icon">
            <GitBranch size={16} />
          </div>

          <div className="skill-row__content">
            <div className="skill-row__title">
              {experiment.skill_name}

              <span
                className={`skill-status skill-status--${experiment.status}`}
              >
                {experiment.status}
              </span>
            </div>

            <div className="skill-row__metadata">
              {experiment.strategy === "thompson"
                ? "Thompson bandit"
                : "A/B test"}

              {" · "}

              {experiment.arms
                .map(
                  (arm) =>
                    `${arm.version}: ` +
                    `${arm.summary?.total ?? 0} runs / ` +
                    `${percent(arm.summary?.success_rate ?? 0)}`,
                )
                .join(" · ")}
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}
