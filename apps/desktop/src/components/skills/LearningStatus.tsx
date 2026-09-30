import {
  Activity,
  AlertCircle,
  Clock,
  Play,
  RefreshCw,
} from "lucide-react";

import { useState } from "react";

import {
  useSkillLearningActions,
  useSkillLearningStatus,
} from "../../hooks/use-settings";

import { relativeTime } from "../../lib/format";

interface Props {
  enabled: boolean;
}

function messageOf(error: unknown): string {
  return error instanceof Error
    ? error.message
    : "Learning status is unavailable.";
}

export function LearningStatus({ enabled }: Props) {
  const [runError, setRunError] = useState<string | null>(null);

  const query = useSkillLearningStatus(enabled);
  const actions = useSkillLearningActions();

  async function runLearning() {
    setRunError(null);

    try {
      await actions.run(true);
    } catch (error) {
      setRunError(messageOf(error));
    }
  }

  if (!enabled) {
    return null;
  }

  if (query.isLoading) {
    return (
      <div className="settings-loading">
        <RefreshCw className="settings-spin" size={16} />
        Loading learning status…
      </div>
    );
  }

  if (query.isError || !query.data) {
    return (
      <div className="settings-error-card">
        <AlertCircle size={16} />

        <span>{query.error ? messageOf(query.error) : "No status yet."}</span>
      </div>
    );
  }

  const status = query.data;
  const threshold = status.trigger_every_successes;
  const inCycle =
    threshold > 0 ? status.success_count_checkpoint % threshold : 0;
  const recent = status.recent_runs.slice(0, 5);
  const runActive =
    (status.run_active ?? status.learning_run_active) === true ||
    actions.running;

  return (
    <section className="skill-learning">
      <div className="skill-learning__heading">
        <h4 className="settings-subsection__heading">Automatic learning</h4>

        <div className="skill-learning__controls">
          <span
            className={`skill-status skill-status--${
              status.worker_running ? "active" : "disabled"
            }`}
          >
            {status.worker_running ? "worker running" : "worker stopped"}
          </span>

          <button
            type="button"
            className="settings-primary-button"
            disabled={!status.enabled || runActive}
            onClick={() => void runLearning()}
          >
            {actions.running ? (
              <RefreshCw className="settings-spin" size={13} />
            ) : (
              <Play size={13} />
            )}
            Run learning
          </button>
        </div>
      </div>

      {runError && (
        <div className="settings-error-card">
          <AlertCircle size={16} />

          <span>{runError}</span>
        </div>
      )}

      <div className="skill-learning__grid">
        <div className="skill-learning__stat">
          <Activity size={15} />

          <strong>{status.total_successful_trajectories}</strong>

          <span>Successful trajectories</span>
        </div>

        <div className="skill-learning__stat">
          <RefreshCw size={15} />

          <strong>{status.pending_successes}</strong>

          <span>Pending (not yet checkpointed)</span>
        </div>

        <div className="skill-learning__stat">
          <Clock size={15} />

          <strong>
            {inCycle}
            {threshold > 0 ? ` / ${threshold}` : ""}
          </strong>

          <span>Since last mining pass</span>
        </div>
      </div>

      {recent.length > 0 && (
        <ul className="skill-learning__runs">
          {recent.map((run) => (
            <li key={run.id}>
              <span
                className={`skill-status skill-status--${
                  run.status === "completed" ||
                  run.status === "succeeded" ||
                  run.status === "success"
                    ? "active"
                    : run.status === "failed" || run.status === "error"
                      ? "rejected"
                      : "candidate"
                }`}
              >
                {run.status}
              </span>

              <span className="skill-learning__counts">
                {run.created_count} created · {run.evaluated_count} evaluated ·{" "}
                {run.promoted_count} promoted
              </span>

              <span className="skill-learning__time">
                {relativeTime(run.started_at)}
              </span>

              {run.error && (
                <span className="skill-learning__error">{run.error}</span>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
