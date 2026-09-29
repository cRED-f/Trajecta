import {
  AlertCircle,
  GitCompare,
  RefreshCw,
  RotateCcw,
} from "lucide-react";

import { useEffect, useState } from "react";

import { settingsApi } from "../../lib/api";

import { relativeTime } from "../../lib/format";

import { useSkillActions, useSkillVersions } from "../../hooks/use-settings";

import { RollbackDialog } from "./RollbackDialog";

import type { SkillVersionCompareResult } from "../../types/settings";

interface Props {
  skillName: string;
}

function messageOf(error: unknown): string {
  return error instanceof Error
    ? error.message
    : "Something went wrong. Please try again.";
}

export function VersionHistory({ skillName }: Props) {
  const versionsQuery = useSkillVersions(skillName);
  const actions = useSkillActions();

  const [rollbackTarget, setRollbackTarget] = useState<string | null>(null);
  const [rollbackError, setRollbackError] = useState<string | null>(null);

  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [comparison, setComparison] =
    useState<SkillVersionCompareResult | null>(null);
  const [compareError, setCompareError] = useState<string | null>(null);
  const [comparing, setComparing] = useState(false);

  // A different skill means different history — drop the previous selection.
  useEffect(() => {
    setRollbackTarget(null);
    setRollbackError(null);
    setFrom("");
    setTo("");
    setComparison(null);
    setCompareError(null);
  }, [skillName]);

  const versions = versionsQuery.data ?? [];
  const active = versions.find((item) => item.status === "active");

  // Newest first; default to comparing the previous version against active.
  const fromValue = from || versions[1]?.version || "";
  const toValue = to || active?.version || versions[0]?.version || "";

  async function runCompare() {
    if (!fromValue || !toValue) {
      return;
    }

    setComparing(true);
    setCompareError(null);

    try {
      setComparison(
        await settingsApi.compareSkillVersions(
          skillName,
          fromValue,
          toValue,
        ),
      );
    } catch (error) {
      setComparison(null);
      setCompareError(messageOf(error));
    } finally {
      setComparing(false);
    }
  }

  async function confirmRollback(reason: string) {
    if (!rollbackTarget) {
      return;
    }

    setRollbackError(null);

    try {
      await actions.rollback({
        skillName,
        version: rollbackTarget,
        reason,
      });

      setRollbackTarget(null);
      setComparison(null);
    } catch (error) {
      setRollbackError(messageOf(error));
    }
  }

  if (versionsQuery.isLoading) {
    return (
      <div className="settings-loading">
        <RefreshCw className="settings-spin" size={16} />
        Loading versions…
      </div>
    );
  }

  if (versionsQuery.isError) {
    return (
      <div className="settings-error-card">
        <AlertCircle size={16} />

        <span>{messageOf(versionsQuery.error)}</span>
      </div>
    );
  }

  if (versions.length === 0) {
    return (
      <div className="settings-empty-state settings-empty-state--small">
        <RotateCcw size={18} />

        <strong>No version history</strong>

        <span>Promote a candidate to record the first version.</span>
      </div>
    );
  }

  return (
    <div className="skill-versions">
      <ul className="skill-version-list">
        {versions.map((version) => (
          <li className="skill-version" key={version.version}>
            <div className="skill-version__main">
              <span className="skill-version__tag">v{version.version}</span>

              <span
                className={`skill-status skill-status--${
                  version.status === "active" ? "active" : "disabled"
                }`}
              >
                {version.status}
              </span>

              <span className="skill-version__time">
                {relativeTime(version.created_at)}
              </span>
            </div>

            {version.status !== "active" && (
              <button
                type="button"
                className="settings-text-button settings-text-button--danger"
                disabled={actions.rollingBack}
                onClick={() => {
                  setRollbackError(null);
                  setRollbackTarget(version.version);
                }}
              >
                <RotateCcw size={13} />
                Rollback
              </button>
            )}
          </li>
        ))}
      </ul>

      {versions.length > 1 && (
        <div className="skill-compare">
          <label htmlFor={`compare-from-${skillName}`}>Compare</label>

          <select
            id={`compare-from-${skillName}`}
            value={fromValue}
            onChange={(event) => setFrom(event.target.value)}
          >
            {versions.map((version) => (
              <option value={version.version} key={version.version}>
                v{version.version}
              </option>
            ))}
          </select>

          <span aria-hidden="true">→</span>

          <label className="sr-only" htmlFor={`compare-to-${skillName}`}>
            Against
          </label>

          <select
            id={`compare-to-${skillName}`}
            value={toValue}
            onChange={(event) => setTo(event.target.value)}
          >
            {versions.map((version) => (
              <option value={version.version} key={version.version}>
                v{version.version}
              </option>
            ))}
          </select>

          <button
            type="button"
            className="settings-text-button"
            disabled={comparing || !fromValue || !toValue}
            onClick={() => void runCompare()}
          >
            {comparing ? (
              <RefreshCw className="settings-spin" size={13} />
            ) : (
              <GitCompare size={13} />
            )}
            Compare
          </button>
        </div>
      )}

      {compareError && (
        <div className="settings-error-card">
          <AlertCircle size={16} />

          <span>{compareError}</span>
        </div>
      )}

      {comparison && (
        <div className="skill-compare-result">
          <strong>
            v{comparison.from.version} → v{comparison.to.version}
          </strong>

          <span>
            {comparison.changed
              ? "Bundle content changed between these versions."
              : "No bundle content difference between these versions."}
          </span>
        </div>
      )}

      {rollbackTarget && (
        <RollbackDialog
          skillName={skillName}
          version={rollbackTarget}
          busy={actions.rollingBack}
          error={rollbackError}
          onConfirm={confirmRollback}
          onCancel={() => {
            setRollbackTarget(null);
            setRollbackError(null);
          }}
        />
      )}
    </div>
  );
}
