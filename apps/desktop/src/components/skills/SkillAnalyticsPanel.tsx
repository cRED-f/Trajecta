import {
  Activity,
  AlertTriangle,
  AlertCircle,
  FlaskConical,
  Hash,
  RefreshCw,
  ShieldCheck,
  Timer,
} from "lucide-react";

import { relativeTime } from "../../lib/format";

import { useSkillAnalytics } from "../../hooks/use-settings";

import type { ReactNode } from "react";

interface Props {
  skillName: string;
}

function messageOf(error: unknown): string {
  return error instanceof Error
    ? error.message
    : "Something went wrong. Please try again.";
}

function Metric({
  icon,
  title,
  value,
  hint,
}: {
  icon: ReactNode;
  title: string;
  value: string;
  hint?: string;
}) {
  return (
    <div className="skill-metric">
      <div className="skill-metric__icon">{icon}</div>

      <div className="skill-metric__body">
        <span className="skill-metric__title">{title}</span>

        <strong className="skill-metric__value">{value}</strong>

        {hint && <span className="skill-metric__hint">{hint}</span>}
      </div>
    </div>
  );
}

/** Performance metrics, regression history and A/B experiments. */
export function SkillAnalyticsPanel({ skillName }: Props) {
  const query = useSkillAnalytics(skillName);

  if (query.isLoading) {
    return (
      <div className="settings-loading">
        <RefreshCw className="settings-spin" size={16} />
        Loading analytics…
      </div>
    );
  }

  if (query.isError) {
    return (
      <div className="settings-error-card">
        <AlertCircle size={16} />

        <span>{messageOf(query.error)}</span>
      </div>
    );
  }

  const data = query.data;

  if (!data) {
    return null;
  }

  const regressions = data.regressions ?? [];
  const experiments = data.experiments ?? [];

  return (
    <div className="skill-analytics">
      {data.total === 0 ? (
        <div className="settings-empty-state settings-empty-state--small">
          <Activity size={18} />

          <strong>No executions recorded</strong>

          <span>
            Metrics appear here after Trajecta runs this skill on a
            trajectory.
          </span>
        </div>
      ) : (
        <>
          <div className="skill-metrics">
            <Metric
              icon={<Activity size={15} />}
              title="Executions"
              value={String(data.total)}
            />

            <Metric
              icon={<ShieldCheck size={15} />}
              title="Success rate"
              value={`${(data.success_rate * 100).toFixed(1)}%`}
            />

            <Metric
              icon={<Timer size={15} />}
              title="Avg latency"
              value={`${Math.round(data.latency)}ms`}
            />

            <Metric
              icon={<Hash size={15} />}
              title="Avg tokens"
              value={String(Math.round(data.tokens))}
            />

            <Metric
              icon={<AlertTriangle size={15} />}
              title="Avg tool failures"
              value={String(Math.round(data.failures * 10) / 10)}
            />
          </div>

          {data.versions.length > 0 && (
            <div className="skill-metric-versions">
              <span className="skill-metric-versions__label">
                Coverage
              </span>

              {data.versions.map((item) => (
                <span className="skill-metric-version" key={item.version}>
                  v{item.version}
                  <em>{item.executions}</em>
                </span>
              ))}
            </div>
          )}
        </>
      )}

      <div className="skill-analytics-section">
        <div className="settings-subsection__heading-row">
          <h6 className="settings-subsection__heading">
            Regression history
          </h6>

          {regressions.length > 0 && (
            <span className="settings-subsection__count">
              {regressions.length}
            </span>
          )}
        </div>

        {regressions.length === 0 ? (
          <p className="skill-analytics-note">
            No regressions detected for this skill.
          </p>
        ) : (
          <ul className="skill-reg-log">
            {regressions.map((entry) => (
              <li
                className={`skill-reg-log__item skill-reg-log__item--${entry.severity}`}
                key={entry.id}
              >
                <div className="skill-reg-log__main">
                  <span className="skill-reg-log__versions">
                    v{entry.bad_version}
                    <em>from v{entry.stable_version}</em>
                  </span>

                  <span className="skill-reg-log__reason">
                    {entry.reason}
                  </span>

                  <span className="skill-reg-log__time">
                    {relativeTime(entry.created_at)}
                  </span>
                </div>

                <span
                  className={`skill-reg-log__severity skill-reg-log__severity--${entry.severity}`}
                >
                  {entry.severity}
                </span>

                <span
                  className={`skill-status skill-status--${
                    entry.rolled_back ? "active" : "rejected"
                  }`}
                >
                  {entry.rolled_back ? "rolled back" : "not restored"}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="skill-analytics-section">
        <div className="settings-subsection__heading-row">
          <h6 className="settings-subsection__heading">
            A/B experiments
          </h6>

          {experiments.length > 0 && (
            <span className="settings-subsection__count">
              {experiments.length}
            </span>
          )}
        </div>

        {experiments.length === 0 ? (
          <p className="skill-analytics-note">
            No version experiments opened for this skill.
          </p>
        ) : (
          <ul className="skill-experiment-list">
            {experiments.map((entry) => (
              <li className="skill-experiment" key={entry.id}>
                <div className="skill-experiment__main">
                  <span className="skill-experiment__split">
                    v{entry.control_version}
                    <em>vs</em>v{entry.experiment_version}
                  </span>

                  <span className="skill-experiment__traffic">
                    {entry.traffic_percent}% to the experiment arm
                  </span>

                  <span className="skill-experiment__time">
                    {relativeTime(entry.created_at)}
                  </span>
                </div>

                <span
                  className={`skill-status skill-status--${
                    entry.status === "running" ? "active" : "disabled"
                  }`}
                >
                  {entry.status}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
