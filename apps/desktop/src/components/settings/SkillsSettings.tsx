import {
  Activity,
  Archive,
  ChevronDown,
  ChevronUp,
  AlertCircle,
  CheckCircle2,
  FlaskConical,
  GitBranch,
  History,
  Play,
  RefreshCw,
  Search,
  Sparkles,
  Star,
  ThumbsDown,
  Zap,
} from "lucide-react";

import { useEffect, useRef, useState } from "react";

import {
  useSkillActions,
  useSkillCatalog,
  useSkillExperiments,
} from "../../hooks/use-settings";

import {
  ExperimentList,
  LearningStatus,
  SkillAnalyticsPanel,
  SkillDependencies,
  VersionHistory,
} from "../skills";

import { SettingsToggle } from "./SettingsToggle";
import { ApiError, settingsApi } from "../../lib/api";
import {
  SkillEvaluationWorkbench,
  advanceSkillLiveState,
  newSkillLiveState,
} from "../skills/SkillEvaluationWorkbench";
import type { SkillEvalEvent, SkillLiveState } from "../skills/SkillEvaluationWorkbench";

import type { SkillCandidate, SkillEvaluationReport } from "../../types/settings";

interface Props {
  enabled: boolean;
  backendOnline: boolean;
}

function messageOf(error: unknown): string {
  return error instanceof Error
    ? error.message
    : "Something went wrong. Please try again.";
}

// Which single candidate a manual action is running for, so only that
// row shows a spinner instead of every row in the list.
type CandidateOperation = {
  candidateId: string;
  kind: "evaluate" | "upgrade";
} | null;

// A failed automated evaluation also sets status="rejected". Only a manual
// Reject action adds rejection_reason: those are the ones we archive here.
function isManuallyRejected(candidate: SkillCandidate): boolean {
  const reason = candidate.metadata?.rejection_reason;
  return (
    candidate.status === "rejected" &&
    typeof reason === "string" &&
    reason.trim().length > 0
  );
}

export function SkillsSettings({ enabled, backendOnline }: Props) {
  const [search, setSearch] = useState("");
  const [showRejected, setShowRejected] = useState(false);

  // Skill whose version history is expanded, plus the last evaluation
  // report seen for each candidate.
  const [historyFor, setHistoryFor] = useState<string | null>(null);
  const [reports, setReports] = useState<
    Record<string, SkillEvaluationReport>
  >({});
  const [actionError, setActionError] = useState<string | null>(null);
  const [liveSessions, setLiveSessions] = useState<Record<string, SkillLiveState>>({});
  const abortController = useRef<AbortController | null>(null);
  const backgroundLastSeq = useRef<Record<string, number>>({});

  const [activeOperation, setActiveOperation] =
    useState<CandidateOperation>(null);

  // Synchronous re-entry guard: state updates are async, so a second
  // click could otherwise start before the first one is reflected.
  const operationLock = useRef(false);

  const query = useSkillCatalog(enabled && backendOnline);
  const experimentsQuery = useSkillExperiments(
    undefined,
    enabled && backendOnline,
  );
  const actions = useSkillActions();

  const summary = query.data?.summary;
  const skills = query.data?.skills ?? [];
  const candidates = query.data?.candidates ?? [];
  const experiments =
    experimentsQuery.data ?? query.data?.experiments ?? [];

  const normalizedSearch = search.trim().toLowerCase();
  const filteredSkills = normalizedSearch
    ? skills.filter(
        (skill) =>
          skill.name.toLowerCase().includes(normalizedSearch) ||
          skill.id.toLowerCase().includes(normalizedSearch),
      )
    : skills;

  const reviewCandidates = candidates.filter(
    (candidate) => !isManuallyRejected(candidate),
  );
  const rejectedCandidates = candidates.filter(isManuallyRejected);

  const matchesCandidateSearch = (candidate: SkillCandidate) =>
    !normalizedSearch ||
    candidate.name.toLowerCase().includes(normalizedSearch) ||
    candidate.description.toLowerCase().includes(normalizedSearch) ||
    (typeof candidate.metadata?.rejection_reason === "string" &&
      candidate.metadata.rejection_reason.toLowerCase().includes(normalizedSearch));

  // A background evaluation already owns the backend run. Subscribe to it;
  // do not start a second evaluation when the user opens this settings page.
  const backgroundIds = candidates
    .filter((candidate) =>
      candidate.status === "evaluating" &&
      activeOperation?.candidateId !== candidate.id,
    )
    .map((candidate) => candidate.id);
  const backgroundKey = backgroundIds.slice().sort().join("|");

  useEffect(() => {
    if (!enabled || !backendOnline || !backgroundKey) return;
    const controllers = backgroundKey.split("|").map((candidateId) => {
      const controller = new AbortController();
      void settingsApi.followBackgroundSkillEvaluation(
        candidateId,
        (event) => {
          if (event.type === "history_truncated") {
            setLiveSessions((current) => ({
              ...current,
              [candidateId]: {
                ...(current[candidateId] ?? newSkillLiveState()),
                activity: [
                  ...(current[candidateId]?.activity ?? []),
                  "Earlier model output is no longer available in the live buffer.",
                ],
              },
            }));
            return;
          }
          if (event.seq != null) {
            if (event.seq <= (backgroundLastSeq.current[candidateId] ?? 0)) return;
            backgroundLastSeq.current[candidateId] = event.seq;
          }
          setLiveSessions((current) => ({
            ...current,
            [candidateId]: advanceSkillLiveState(
              current[candidateId] ?? newSkillLiveState(), event,
            ),
          }));
          if (event.type === "completed" && event.report) {
            setReports((current) => ({
              ...current,
              [candidateId]: event.report!,
            }));
            void query.refetch();
          }
          if (event.type === "error") void query.refetch();
        },
        controller.signal,
      ).catch((error: unknown) => {
        if (controller.signal.aborted) return;
        // When upgrading from an older backend, retain the existing
        // "automatic evaluation" status rather than crashing the page.
        if (error instanceof ApiError && error.status === 404) return;
        setLiveSessions((current) => ({
          ...current,
          [candidateId]: {
            ...(current[candidateId] ?? newSkillLiveState()),
            active: false,
            phase: "finished",
            error: messageOf(error),
          },
        }));
      });
      return controller;
    });
    return () => controllers.forEach((controller) => controller.abort());
  }, [enabled, backendOnline, backgroundKey]);

  const filteredCandidates = reviewCandidates.filter(matchesCandidateSearch);
  const filteredRejectedCandidates = rejectedCandidates.filter(matchesCandidateSearch);

  const filteredExperiments = normalizedSearch
    ? experiments.filter((experiment) =>
        experiment.skill_name.toLowerCase().includes(normalizedSearch),
      )
    : experiments;

  const busy =
    activeOperation !== null ||
    actions.evaluating ||
    actions.promoting ||
    actions.upgrading ||
    actions.rejecting ||
    actions.startingExperiment;

  async function rejectCandidate(candidateId: string) {
    if (busy) return;
    setActionError(null);

    try {
      // The backend marks it rejected and retains the evaluation records.
      await actions.reject({
        candidateId,
        reason: "Manually rejected from Skills settings.",
      });
      // The React Query mutation invalidates the catalog after success.
      // Open the archive so the user sees where the candidate moved.
      setShowRejected(true);
    } catch (error) {
      setActionError(messageOf(error));
    }
  }

  async function startCandidateExperiment(
    candidateId: string,
    strategy: "ab" | "thompson",
  ) {
    setActionError(null);

    try {
      await actions.startExperiment({ candidateId, strategy });
    } catch (error) {
      setActionError(messageOf(error));
    }
  }

  async function runCandidate(candidateId: string, kind: "evaluate" | "upgrade") {
    if (operationLock.current) return;
    operationLock.current = true;
    const controller = new AbortController();
    abortController.current = controller;
    setActiveOperation({ candidateId, kind });
    setActionError(null);
    setReports((current) => {
      const next = { ...current };
      delete next[candidateId];
      return next;
    });
    setLiveSessions((current) => ({ ...current, [candidateId]: newSkillLiveState() }));
    let completed = false;
    let serverError: string | null = null;

    try {
      await settingsApi.streamSkillEvaluation(
        candidateId,
        kind === "upgrade",
        (event: SkillEvalEvent) => {
          setLiveSessions((current) => ({
            ...current,
            [candidateId]: advanceSkillLiveState(
              current[candidateId] ?? newSkillLiveState(), event,
            ),
          }));
          if (event.type === "completed" && event.report) {
            completed = true;
            setReports((current) => ({ ...current, [candidateId]: event.report! }));
          }
          if (event.type === "error") {
            serverError = event.message ?? "Evaluation failed";
          }
        },
        controller.signal,
      );
      if (serverError) throw new Error(serverError);
      if (!completed) throw new Error("Evaluation stream ended unexpectedly.");
    } catch (error) {
      const message = controller.signal.aborted
        ? "Evaluation stopped by user."
        : messageOf(error);
      setActionError(message);
      setLiveSessions((current) => ({
        ...current,
        [candidateId]: {
          ...(current[candidateId] ?? newSkillLiveState()),
          active: false,
          phase: "finished",
          error: message,
        },
      }));
    } finally {
      operationLock.current = false;
      abortController.current = null;
      setActiveOperation(null);
      void query.refetch();
    }
  }

  function stopCandidate() {
    abortController.current?.abort();
  }

  async function evaluateCandidate(candidateId: string) {
    await runCandidate(candidateId, "evaluate");
  }

  async function upgradeCandidate(candidateId: string) {
    await runCandidate(candidateId, "upgrade");
  }

  return (
    <section>
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
                  <GitBranch size={16} />
                </div>
                <strong>{summary.experimenting ?? 0}</strong>
                <span>Experimenting</span>
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
                  Registered skills
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
                          {historyFor === skill.name ? "Hide" : "Details"}
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
                      Performance · {historyFor}
                    </h5>
                  </div>

                  <SkillAnalyticsPanel skillName={historyFor} />

                  <div className="settings-subsection__heading-row">
                    <h5 className="settings-subsection__heading">
                      Version history · {historyFor}
                    </h5>
                  </div>

                  <VersionHistory skillName={historyFor} />

                  <SkillDependencies
                    skillName={historyFor}
                    skillNames={skills.map((item) => item.name)}
                  />
                </div>
              )}
            </div>
          )}

          {!query.isLoading && !query.isError && (
            <div className="settings-subsection">
              <div className="settings-subsection__heading-row">
                <h4 className="settings-subsection__heading">
                  Skills waiting for review
                </h4>

                {reviewCandidates.length > 0 && (
                  <span className="settings-subsection__count">
                    {reviewCandidates.length} learned workflows
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

                    const isEvaluating =
                      (activeOperation?.candidateId === candidate.id &&
                        activeOperation.kind === "evaluate") ||
                      candidate.status === "evaluating";

                    const isUpgrading =
                      activeOperation?.candidateId === candidate.id &&
                      activeOperation.kind === "upgrade";

                    const isProcessing = isEvaluating || isUpgrading;

                    return (
                    <div
                      key={candidate.id}
                      className={`skill-row ${
                        isProcessing ? "skill-row--processing" : ""
                      }`}
                      aria-busy={isProcessing}
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
                            {candidate.status === "candidate" ? "Needs testing" :
                             candidate.status === "verified" ? "Passed tests" :
                             candidate.status === "rejected" ? "Did not pass" :
                             candidate.status === "evaluating" ? "Testing" : candidate.status}
                          </span>
                        </div>

                        <div className="skill-row__metadata">
                          {candidate.description || "No description"}
                        </div>

                        {candidate.status === "evaluating" && !liveSessions[candidate.id] && (
                          <div className="skill-evaluation-progress" role="status">
                            <span className="skill-evaluation-progress__label">
                              <RefreshCw size={13} className="settings-spin" />
                              Automatic evaluation running. Live output is available for tests started here.
                            </span>
                          </div>
                        )}
                        <SkillEvaluationWorkbench
                          name={candidate.name}
                          live={liveSessions[candidate.id]}
                          report={report}
                          onStop={isProcessing && activeOperation?.candidateId === candidate.id ? stopCandidate : undefined}
                        />
                      </div>

                      <div className="skill-row__actions">
                        {candidate.status === "candidate" && (
                          <button
                            type="button"
                            className="settings-primary-button"
                            disabled={busy}
                            onClick={() => void upgradeCandidate(candidate.id)}
                          >
                            {isUpgrading ? (
                              <>
                                <RefreshCw
                                  className="settings-spin"
                                  size={13}
                                />
                                Upgrading...
                              </>
                            ) : (
                              <>
                                <Zap size={13} />
                                Test & activate
                              </>
                            )}
                          </button>
                        )}

                        {(candidate.status === "candidate" ||
                          candidate.status === "verified" ||
                          candidate.status === "rejected") && (
                          <button
                            type="button"
                            className="settings-text-button"
                            disabled={busy}
                            onClick={() =>
                              void evaluateCandidate(candidate.id)
                            }
                          >
                            {isEvaluating ? (
                              <>
                                <RefreshCw
                                  className="settings-spin"
                                  size={13}
                                />
                                Evaluating...
                              </>
                            ) : (
                              <>
                                <Play size={13} />
                                Test skill
                              </>
                            )}
                          </button>
                        )}

                        {candidate.status === "verified" && (
                          <>
                            <button
                              type="button"
                              className="settings-text-button"
                              disabled={busy}
                              onClick={() =>
                                void startCandidateExperiment(
                                  candidate.id,
                                  "ab",
                                )
                              }
                            >
                              <GitBranch size={13} />
                              A/B
                            </button>

                            <button
                              type="button"
                              className="settings-text-button"
                              disabled={busy}
                              onClick={() =>
                                void startCandidateExperiment(
                                  candidate.id,
                                  "thompson",
                                )
                              }
                            >
                              <Activity size={13} />
                              Bandit
                            </button>

                            <button
                              type="button"
                              className="settings-primary-button"
                              disabled={actions.promoting}
                              onClick={() =>
                                void actions.promote(candidate.id)
                              }
                            >
                              {actions.promoting ? (
                                <RefreshCw
                                  className="settings-spin"
                                  size={13}
                                />
                              ) : (
                                <Star size={13} />
                              )}
                              Promote now
                            </button>
                          </>
                        )}

                        <button
                          type="button"
                          className="settings-text-button settings-text-button--danger"
                          disabled={busy}
                          title="Move this candidate to the Rejected section"
                          onClick={() => void rejectCandidate(candidate.id)}
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

          {!query.isLoading && !query.isError && (
            <div className="settings-subsection skill-rejected-section">
              <div className="settings-subsection__heading-row">
                <div>
                  <h4 className="settings-subsection__heading">
                    Rejected
                    <span className="settings-subsection__count">
                      {` (${rejectedCandidates.length})`}
                    </span>
                  </h4>
                  <p className="skill-rejected-section__description">
                    Candidates you rejected are kept for history, not deleted.
                    Unsuccessful automatic evaluations remain in review for retry.
                  </p>
                </div>
                <button
                  type="button"
                  className="settings-text-button"
                  aria-expanded={showRejected}
                  onClick={() => setShowRejected((previous) => !previous)}
                >
                  {showRejected ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                  {showRejected ? "Hide" : "Show"}
                </button>
              </div>

              {showRejected && (
                filteredRejectedCandidates.length === 0 ? (
                  <div className="settings-empty-state settings-empty-state--small">
                    <Archive size={20} />
                    <strong>No rejected candidates</strong>
                    <span>
                      {search ? "Nothing matches your search." : "Candidates you reject will appear here."}
                    </span>
                  </div>
                ) : (
                  <div className="skill-list">
                    {filteredRejectedCandidates.map((candidate) => (
                      <div className="skill-row" key={candidate.id}>
                        <div className="skill-row__icon">
                          <Archive size={16} />
                        </div>
                        <div className="skill-row__content">
                          <div className="skill-row__title">
                            {candidate.name}
                            <span className="skill-status skill-status--rejected">
                              Rejected
                            </span>
                          </div>
                          <div className="skill-row__metadata">
                            {candidate.description || "No description"}
                          </div>
                          <div className="skill-rejected-section__reason">
                            <strong>Reason:</strong>{" "}
                            {String(candidate.metadata.rejection_reason)}
                          </div>
                          <div className="skill-rejected-section__date">
                            Last updated: {new Date(candidate.updated_at).toLocaleString()}
                          </div>
                          <SkillEvaluationWorkbench
                            name={candidate.name}
                            live={liveSessions[candidate.id]}
                            report={reports[candidate.id]}
                          />
                        </div>
                      </div>
                    ))}
                  </div>
                )
              )}
            </div>
          )}

          {!query.isLoading && !query.isError && (
            <div className="settings-subsection">
              <div className="settings-subsection__heading-row">
                <h4 className="settings-subsection__heading">Experiments</h4>

                {experiments.length > 0 && (
                  <span className="settings-subsection__count">
                    {experiments.length}
                  </span>
                )}
              </div>

              {experimentsQuery.isLoading && experiments.length === 0 ? (
                <div className="settings-loading">
                  <RefreshCw className="settings-spin" size={17} />
                  Loading experiments…
                </div>
              ) : filteredExperiments.length === 0 ? (
                <div className="settings-empty-state settings-empty-state--small">
                  <GitBranch size={20} />

                  <strong>No experiments</strong>

                  <span>
                    {search
                      ? "Nothing matches your search."
                      : "Open an A/B or bandit split from a verified candidate above."}
                  </span>
                </div>
              ) : (
                <ExperimentList experiments={filteredExperiments} />
              )}
            </div>
          )}
        </>
      )}
    </section>
  );
}