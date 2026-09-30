import {
  Activity,
  AlertCircle,
  Hash,
  RefreshCw,
  ShieldCheck,
  Stethoscope,
  Timer,
} from "lucide-react";

import { useEffect, useState } from "react";

import { useQueryClient } from "@tanstack/react-query";

import { settingsApi } from "../../lib/api";

import { percent, relativeTime } from "../../lib/format";

import {
  settingsQueryKeys,
  useSkillAnalytics,
} from "../../hooks/use-settings";

import { ExperimentList } from "./ExperimentList";

interface Props {
  skillName: string;
}

function messageOf(error: unknown): string {
  return error instanceof Error
    ? error.message
    : "Something went wrong. Please try again.";
}

function stringList(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : [];
}

/** Per-version metrics, regression history and version experiments. */
export function SkillAnalyticsPanel({ skillName }: Props) {
  const queryClient = useQueryClient();
  const query = useSkillAnalytics(skillName);

  const [checking, setChecking] = useState(false);
  const [checkResult, setCheckResult] = useState<
    Record<string, unknown> | null
  >(null);
  const [checkError, setCheckError] = useState<string | null>(null);

  // A different skill means a different regression history.
  useEffect(() => {
    setCheckResult(null);
    setCheckError(null);
    setChecking(false);
  }, [skillName]);

  async function checkRegression() {
    setChecking(true);
    setCheckError(null);

    try {
      setCheckResult(await settingsApi.checkSkillRegression(skillName));

      await queryClient.invalidateQueries({
        queryKey: settingsQueryKeys.skillAnalytics(skillName),
      });
      await queryClient.invalidateQueries({
        queryKey: settingsQueryKeys.skills,
      });
    } catch (error) {
      setCheckResult(null);
      setCheckError(messageOf(error));
    } finally {
      setChecking(false);
    }
  }

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

  const versions = data.versions ?? [];
  const regressions = data.regressions ?? [];
  const experiments = data.experiments ?? [];

  // Metrics of the version that is serving traffic, else the newest one.
  const current =
    versions.find((item) => item.status === "active") ?? versions[0];
  const metrics = current?.metrics;

  const checked = checkResult?.checked === true;
  const regressed = checkResult?.regression === true;
  const reasons = stringList(checkResult?.reasons);
  const rollbackError =
    typeof checkResult?.rollback_error === "string"
      ? checkResult.rollback_error
      : null;

  return (
    <div className="skill-analytics">
      {!metrics ? (
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
          <div className="skill-summary-grid">
            <div className="skill-summary">
              <div className="skill-summary__icon">
                <ShieldCheck size={16} />
              </div>

              <strong>{percent(metrics.success_rate)}</strong>

              <span>Success</span>
            </div>

            <div className="skill-summary">
              <div className="skill-summary__icon">
                <Activity size={16} />
              </div>

              <strong>{metrics.total}</strong>

              <span>Observed runs</span>
            </div>

            <div className="skill-summary">
              <div className="skill-summary__icon">
                <Timer size={16} />
              </div>

              <strong>
                {metrics.average_duration_seconds.toFixed(2)}s
              </strong>

              <span>Avg latency</span>
            </div>

            <div className="skill-summary">
              <div className="skill-summary__icon">
                <Hash size={16} />
              </div>

              <strong>{Math.round(metrics.average_total_tokens)}</strong>

              <span>Avg tokens</span>
            </div>
          </div>

          {versions.length > 0 && (
            <div className="skill-metric-versions">
              <span className="skill-metric-versions__label">Coverage</span>

              {versions.map((item) => (
                <span className="skill-metric-version" key={item.version}>
                  v{item.version}
                  <em>{item.metrics?.total ?? 0}</em>
                </span>
              ))}
            </div>
          )}
        </>
      )}

      <div className="skill-analytics-section">
        <div className="settings-subsection__heading-row">
          <h6 className="settings-subsection__heading">Check regression</h6>

          <button
            type="button"
            className="settings-text-button"
            disabled={checking}
            onClick={() => void checkRegression()}
          >
            {checking ? (
              <RefreshCw className="settings-spin" size={13} />
            ) : (
              <Stethoscope size={13} />
            )}
            Run check
          </button>
        </div>

        {checkError && (
          <div className="settings-error-card">
            <AlertCircle size={16} />

            <span>{checkError}</span>
          </div>
        )}

        {checkResult && (
          <div className="skill-compare-result">
            <strong>
              <span
                className={`skill-status skill-status--${
                  !checked
                    ? "disabled"
                    : regressed
                      ? "rejected"
                      : "verified"
                }`}
              >
                {!checked
                  ? "skipped"
                  : regressed
                    ? "regression"
                    : "healthy"}
              </span>
            </strong>

            <span>
              {reasons.length > 0
                ? reasons.join(" · ")
                : typeof checkResult.reason === "string"
                  ? checkResult.reason
                  : regressed
                    ? "The current version regressed against the stable one."
                    : "No regression detected against the stable version."}
            </span>

            {rollbackError && <span>{rollbackError}</span>}
          </div>
        )}
      </div>

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
                    {entry.reasons?.length
                      ? entry.reasons.join(" · ")
                      : "regression detected"}
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
          <h6 className="settings-subsection__heading">Version experiments</h6>

          {experiments.length > 0 && (
            <span className="settings-subsection__count">
              {experiments.length}
            </span>
          )}
        </div>

        <ExperimentList
          experiments={experiments}
          emptyNote="No version experiments opened for this skill."
        />
      </div>
    </div>
  );
}
