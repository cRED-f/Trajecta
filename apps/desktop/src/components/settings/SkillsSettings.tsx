import {
  AlertCircle,
  CheckCircle2,
  FlaskConical,
  History,
  Play,
  RefreshCw,
  Search,
  Sparkles,
  Star,
  ThumbsDown,
  Zap,
} from "lucide-react";

import { useState } from "react";

import { useSkillActions, useSkillCatalog } from "../../hooks/use-settings";

import {
  LearningStatus,
  RegressionBadge,
  VersionHistory,
  evaluationRegression,
} from "../skills";

import { SettingsToggle } from "./SettingsToggle";

import type { SkillEvaluationReport } from "../../types/settings";

interface Props {
  enabled: boolean;
  backendOnline: boolean;
}

function messageOf(error: unknown): string {
  return error instanceof Error
    ? error.message
    : "Something went wrong. Please try again.";
}

export function SkillsSettings({ enabled, backendOnline }: Props) {
  const [search, setSearch] = useState("");

  // Skill whose version history is expanded, plus the last evaluation
  // report seen for each candidate.
  const [historyFor, setHistoryFor] = useState<string | null>(null);
  const [reports, setReports] = useState<
    Record<string, SkillEvaluationReport>
  >({});
  const [actionError, setActionError] = useState<string | null>(null);

  const query = useSkillCatalog(enabled && backendOnline);
  const actions = useSkillActions();

  const summary = query.data?.summary;
  const skills = query.data?.skills ?? [];
  const candidates = query.data?.candidates ?? [];

  const normalizedSearch = search.trim().toLowerCase();
  const filteredSkills = normalizedSearch
    ? skills.filter(
        (skill) =>
          skill.name.toLowerCase().includes(normalizedSearch) ||
          skill.id.toLowerCase().includes(normalizedSearch),
      )
    : skills;

  const filteredCandidates = normalizedSearch
    ? candidates.filter(
        (candidate) =>
          candidate.name.toLowerCase().includes(normalizedSearch) ||
          candidate.description.toLowerCase().includes(normalizedSearch),
      )
    : candidates;

  const busy =
    actions.evaluating ||
    actions.promoting ||
    actions.upgrading ||
    actions.rejecting;

  async function evaluateCandidate(candidateId: string) {
    setActionError(null);

    try {
      const report = await actions.evaluate(candidateId);

      setReports((previous) => ({ ...previous, [candidateId]: report }));
    } catch (error) {
      setActionError(messageOf(error));
    }
  }

  async function upgradeCandidate(candidateId: string) {
    setActionError(null);

    try {
      const result = await actions.upgrade({
        candidateId,
        reason: "Upgrade requested from the Skills page.",
      });

      if (result.status === "rejected") {
        setActionError(
          `Upgrade blocked: the evaluation did not pass (${result.reason ?? "evaluation_failed"}).`,
        );
      } else {
        // The promoted candidate leaves the list; forget its report.
        setReports((previous) => {
          const next = { ...previous };
          delete next[candidateId];
          return next;
        });
      }
    } catch (error) {
      setActionError(messageOf(error));
    }
  }

  return (
    <section>
      <div className="settings-page-header">
        <div>
          <h3>Skills</h3>
          <p>
            Verified skills Trajecta has learned, plus candidates awaiting
            evaluation.
          </p>
        </div>
      </div>

      {!backendOnline ? (
        <div className="settings-empty-state">
          <AlertCircle size={22} />

          <strong>Backend disconnected</strong>

          <span>Connect to the Trajecta backend to manage skills.</span>
        </div>
      ) : (
        <>
          <LearningStatus enabled={enabled} />

          {actionError && (
            <div className="settings-error-card">
              <AlertCircle size={17} />

              <span>{actionError}</span>
            </div>
          )}

          {summary && (
            <div className="skill-summary-grid">
              <div className="skill-summary">
                <div className="skill-summary__icon">
                  <CheckCircle2 size={16} />
                </div>
                <strong>{summary.active}</strong>
                <span>Active</span>
              </div>

              <div className="skill-summary">
                <div className="skill-summary__icon">
                  <FlaskConical size={16} />
                </div>
                <strong>{summary.evaluating}</strong>
                <span>Evaluating</span>
              </div>

              <div className="skill-summary">
                <div className="skill-summary__icon">
                  <Sparkles size={16} />
                </div>
                <strong>{summary.candidate}</strong>
                <span>Candidates</span>
              </div>

              <div className="skill-summary">
                <div className="skill-summary__icon">
                  <Star size={16} />
                </div>
                <strong>{summary.disabled}</strong>
                <span>Disabled</span>
              </div>
            </div>
          )}

          <div className="mcp-search settings-search">
            <Search size={15} />

            <input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Search skills and candidates..."
            />
          </div>

          {query.isLoading && (
            <div className="settings-loading">
              <RefreshCw className="settings-spin" size={17} />
              Loading skills…
            </div>
          )}

          {query.isError && (
            <div className="settings-error-card">
              <AlertCircle size={17} />

              <span>
                {query.error instanceof Error
                  ? query.error.message
                  : "Failed to load skills."}
              </span>
            </div>
          )}

          {!query.isLoading && !query.isError && (
            <div className="settings-subsection">
              <div className="settings-subsection__heading-row">
                <h4 className="settings-subsection__heading">
                  Active skills
                </h4>

                {skills.length > 0 && (
                  <span className="settings-subsection__count">
                    {skills.length} registered
                  </span>
                )}
              </div>

              {filteredSkills.length === 0 ? (
                <div className="settings-empty-state settings-empty-state--small">
                  <Sparkles size={20} />

                  <strong>No registered skills</strong>

                  <span>
                    {search
                      ? "Nothing matches your search."
                      : "Promote a candidate below to register a verified skill."}
                  </span>
                </div>
              ) : (
                <div className="skill-list">
                  {filteredSkills.map((skill) => (
                    <div className="skill-row" key={skill.id}>
                      <div className="skill-row__icon">
                        <Sparkles size={16} />
                      </div>

                      <div className="skill-row__content">
                        <div className="skill-row__title">
                          {skill.name}
                          <span
                            className={`skill-status skill-status--${skill.status}`}
                          >
                            {skill.status}
                          </span>
                        </div>

                        <div className="skill-row__metadata">
                          v{skill.version}
                        </div>
                      </div>

                      <div className="skill-row__actions">
                        <button
                          type="button"
                          className="settings-text-button"
                          aria-expanded={historyFor === skill.name}
                          onClick={() =>
                            setHistoryFor(
                              historyFor === skill.name ? null : skill.name,
                            )
                          }
                        >
                          <History size={13} />
                          {historyFor === skill.name ? "Hide" : "History"}
                        </button>

                        <SettingsToggle
                          checked={skill.status === "active"}
                          disabled={actions.togglingEnabled}
                          label={`Enable ${skill.name}`}
                          onChange={(enabled) =>
                            void actions.setEnabled({
                              skillName: skill.name,
                              enabled,
                            })
                          }
                        />
                      </div>
                    </div>
                  ))}
                </div>
              )}

              {historyFor && skills.some((s) => s.name === historyFor) && (
                <div className="skill-history">
                  <div className="settings-subsection__heading-row">
                    <h5 className="settings-subsection__heading">
                      Version history · {historyFor}
                    </h5>
                  </div>

                  <VersionHistory skillName={historyFor} />
                </div>
              )}
            </div>
          )}

          {!query.isLoading && !query.isError && (
            <div className="settings-subsection">
              <div className="settings-subsection__heading-row">
                <h4 className="settings-subsection__heading">
                  Candidates
                </h4>

                {candidates.length > 0 && (
                  <span className="settings-subsection__count">
                    {candidates.length} pending review
                  </span>
                )}
              </div>

              {filteredCandidates.length === 0 ? (
                <div className="settings-empty-state settings-empty-state--small">
                  <FlaskConical size={20} />

                  <strong>No candidates</strong>

                  <span>
                    New skill candidates will appear here after Trajecta learns
                    a task.
                  </span>
                </div>
              ) : (
                <div className="skill-list">
                  {filteredCandidates.map((candidate) => {
                    const report = reports[candidate.id];

                    return (
                    <div
                      className="skill-row"
                      key={candidate.id}
                    >
                      <div className="skill-row__icon">
                        <FlaskConical size={16} />
                      </div>

                      <div className="skill-row__content">
                        <div className="skill-row__title">
                          {candidate.name}
                          <span
                            className={`skill-status skill-status--${candidate.status}`}
                          >
                            {candidate.status}
                          </span>
                        </div>

                        <div className="skill-row__metadata">
                          {candidate.description || "No description"}
                        </div>

                        {report && (
                          <div className="skill-eval">
                            <div className="skill-eval__head">
                              <span
                                className={`skill-status skill-status--${
                                  report.verdict === "pass"
                                    ? "verified"
                                    : "rejected"
                                }`}
                              >
                                {report.verdict}
                              </span>

                              <RegressionBadge
                                regression={evaluationRegression(report)}
                              />

                              <span className="skill-eval__rate">
                                {Math.round(
                                  report.candidate.success_rate * 100,
                                )}
                                % vs baseline{" "}
                                {Math.round(
                                  report.baseline.success_rate * 100,
                                )}
                                %
                              </span>
                            </div>

                            <div className="skill-eval__metrics">
                              <span>
                                {report.candidate.successes}/
                                {report.candidate.total_cases} cases
                              </span>

                              <span>
                                {report.candidate.tool_errors} tool errors
                                (baseline {report.baseline.tool_errors})
                              </span>
                            </div>
                          </div>
                        )}
                      </div>

                      <div className="skill-row__actions">
                        {candidate.status === "candidate" && (
                          <button
                            type="button"
                            className="settings-primary-button"
                            disabled={busy}
                            onClick={() => void upgradeCandidate(candidate.id)}
                          >
                            {actions.upgrading ? (
                              <RefreshCw className="settings-spin" size={13} />
                            ) : (
                              <Zap size={13} />
                            )}
                            Upgrade
                          </button>
                        )}

                        {(candidate.status === "candidate" ||
                          candidate.status === "verified") && (
                          <button
                            type="button"
                            className="settings-text-button"
                            disabled={busy}
                            onClick={() =>
                              void evaluateCandidate(candidate.id)
                            }
                          >
                            {actions.evaluating ? (
                              <RefreshCw className="settings-spin" size={13} />
                            ) : (
                              <Play size={13} />
                            )}
                            Evaluate
                          </button>
                        )}

                        {candidate.status === "verified" && (
                          <button
                            type="button"
                            className="settings-primary-button"
                            disabled={busy}
                            onClick={() =>
                              void actions.promote(candidate.id)
                            }
                          >
                            {actions.promoting ? (
                              <RefreshCw className="settings-spin" size={13} />
                            ) : (
                              <Star size={13} />
                            )}
                            Promote
                          </button>
                        )}

                        <button
                          type="button"
                          className="settings-text-button settings-text-button--danger"
                          disabled={actions.rejecting}
                          onClick={() =>
                            void actions.reject({
                              candidateId: candidate.id,
                              reason: "Rejected from the Skills settings page.",
                            })
                          }
                        >
                          <ThumbsDown size={13} />
                          Reject
                        </button>
                      </div>
                    </div>
                    );
                  })}
                </div>
              )}
            </div>
          )}
        </>
      )}
    </section>
  );
}