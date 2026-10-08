import { useEffect, useState } from "react";
import { AlertTriangle, CheckCircle2, ChevronDown, LoaderCircle, Terminal } from "lucide-react";
import type { SkillEvaluationReport } from "../../types/settings";

export interface SkillEvalEvent {
  type: string;
  case_id?: string;
  task?: string;
  variant?: "baseline" | "candidate";
  repetition?: number;
  model?: string;
  text?: string;
  name?: string;
  status?: string;
  total_cases?: number;
  error?: string | null;
  verdict?: string;
  report?: SkillEvaluationReport;
  success?: boolean | null;
  skipped?: boolean;
  message?: string;
  judge_reason?: string | null;
  score?: number | null;
  skill?: string;
  version?: string;
}

export interface SkillLiveState {
  active: boolean;
  phase: "preparing" | "testing" | "deciding" | "promoting" | "finished";
  totalCases: number;
  finishedCases: number;
  caseId: string;
  task: string;
  repetition: number;
  variant: "baseline" | "candidate";
  model: string;
  output: Record<string, { baseline: string; candidate: string }>;
  activity: string[];
  error: string | null;
  promoted: string | null;
}

export function newSkillLiveState(): SkillLiveState {
  return {
    active: true, phase: "preparing", totalCases: 0, finishedCases: 0,
    caseId: "", task: "", repetition: 1, variant: "baseline",
    model: "Waiting for model", output: {}, activity: [], error: null,
    promoted: null,
  };
}

export function advanceSkillLiveState(old: SkillLiveState, event: SkillEvalEvent): SkillLiveState {
  const next = { ...old };
  const caseKey = `${event.case_id ?? old.caseId}:${event.repetition ?? old.repetition}`;
  switch (event.type) {
    case "evaluation_started":
      next.phase = "testing";
      next.totalCases = event.total_cases ?? 0;
      break;
    case "case_started":
      next.caseId = event.case_id ?? "";
      next.task = event.task ?? "";
      break;
    case "run_started":
      next.variant = event.variant ?? "baseline";
      next.repetition = event.repetition ?? 1;
      next.caseId = event.case_id ?? next.caseId;
      break;
    case "model_selected":
      next.model = event.model ?? "Unknown model";
      break;
    case "model_delta": {
      const variant = event.variant ?? next.variant;
      const current = old.output[caseKey] ?? { baseline: "", candidate: "" };
      next.output = {
        ...old.output,
        [caseKey]: { ...current, [variant]: (current[variant] + (event.text ?? "")).slice(-12000) },
      };
      break;
    }
    case "tool_event":
      next.activity = [...old.activity, `${event.variant === "baseline" ? "Original" : "New skill"}: ${event.name ?? "tool"} (${event.status ?? "finished"})`].slice(-12);
      break;
    case "run_finished":
      if (event.score != null || event.judge_reason) {
        const role = event.variant === "baseline" ? "Original" : "New skill";
        next.activity = [...old.activity, `${role} scored${event.score != null ? ` ${Math.round(event.score * 100)}%` : ""}${event.judge_reason ? ` — ${event.judge_reason}` : ""}`].slice(-12);
      }
      if (event.skipped) {
        next.activity = [...old.activity, `${event.case_id}: test unavailable${event.error ? ` — ${event.error}` : ""}`].slice(-12);
      }
      break;
    case "case_finished":
      next.finishedCases = Math.min(old.totalCases || Infinity, old.finishedCases + 1);
      break;
    case "evaluation_finished":
      next.phase = "deciding";
      break;
    case "promotion_started":
      next.phase = "promoting";
      break;
    case "promoted":
      next.promoted = `${event.skill ?? "Skill"} v${event.version ?? "?"}`;
      break;
    case "completed":
      next.phase = "finished";
      next.active = false;
      break;
    case "error":
      next.active = false;
      next.phase = "finished";
      next.error = event.message ?? "Evaluation could not finish";
      break;
  }
  return next;
}

function rate(report: SkillEvaluationReport, variant: "baseline" | "candidate"): string {
  const data = report[variant];
  return data.graded_runs > 0 ? `${Math.round(data.success_rate * 100)}%` : "Not tested";
}

function friendlyVerdict(verdict: string): string {
  if (verdict === "pass") return "Passed — ready to use";
  if (verdict === "needs_review") return "Needs more evidence";
  if (verdict === "fail") return "Did not pass the tests";
  return `Result: ${verdict}`;
}

export function SkillEvaluationWorkbench({
  name, live, report, onStop,
}: {
  name: string;
  live?: SkillLiveState;
  report?: SkillEvaluationReport;
  onStop?: () => void;
}) {
  const [tab, setTab] = useState<"baseline" | "candidate">("candidate");
  const [showTechnical, setShowTechnical] = useState(false);
  const [selectedCase, setSelectedCase] = useState("");
  useEffect(() => {
    if (live?.active) setTab(live.variant);
  }, [live?.variant, live?.caseId, live?.repetition]);
  if (!live && !report) return null;

  const key = `${live?.caseId ?? ""}:${live?.repetition ?? 1}`;
  const outputKeys = Object.keys(live?.output ?? {});
  const historyKey = selectedCase && live?.output[selectedCase]
    ? selectedCase
    : outputKeys[outputKeys.length - 1] ?? key;
  const visibleText = live?.output[key]?.[tab] ?? "";
  const running = Boolean(live?.active);
  const verdict = report?.verdict;

  return (
    <section className="eval-workbench" aria-label={`Evaluation for ${name}`}>
      <header className="eval-workbench__header">
        <div>
          <strong>{running ? "Testing this skill" : report ? friendlyVerdict(verdict ?? "unknown") : "Evaluation details"}</strong>
          <p>{running ? "Comparing Trajecta's original approach with this learned skill in isolated test runs." : verdict === "pass" ? "The candidate passed the required safety, reliability, generalization, and improvement checks." : "Review the results to understand what prevented approval."}</p>
        </div>
        {running ? <LoaderCircle className="settings-spin" size={17} aria-label="Running" /> : verdict === "pass" ? <CheckCircle2 size={19} /> : <AlertTriangle size={19} />}
      </header>

      <div className="eval-workbench__steps" aria-label="Evaluation stages">
        <span className={live?.phase === "preparing" ? "is-current" : ""}>1. Prepare tests</span>
        <span className={live?.phase === "testing" ? "is-current" : ""}>2. Compare approaches</span>
        <span className={live?.phase === "deciding" || live?.phase === "promoting" || live?.phase === "finished" ? "is-current" : ""}>3. Review results</span>
      </div>

      {running && live && (
        <>
          <div className="eval-workbench__progress" role="status" aria-live="polite">
            <div><strong>{live.phase === "promoting" ? "Activating verified skill" : `Test ${Math.min(live.finishedCases + 1, live.totalCases || 1)} of ${live.totalCases || "?"}`}</strong><span>{live.variant === "baseline" ? "Original approach" : "New skill"} · Run {live.repetition}</span></div>
            <div className="eval-workbench__bar" aria-hidden="true"><span /></div>
            <p>{live.task || "Preparing the next test..."}</p>
          </div>

          <div className="eval-workbench__transcript">
            <div className="eval-workbench__transcript-title"><Terminal size={14} /><strong>Live model output</strong><span>{live.model}</span></div>
            <div className="eval-workbench__tabs" role="group" aria-label="Model run">
              <button type="button" className={tab === "baseline" ? "is-selected" : ""} onClick={() => setTab("baseline")}>Original approach</button>
              <button type="button" className={tab === "candidate" ? "is-selected" : ""} onClick={() => setTab("candidate")}>New skill</button>
            </div>
            <pre aria-live="off">{visibleText || `Waiting for ${tab === "baseline" ? "original" : "new skill"} model output...`}</pre>
            <small>Shows generated answer text only; private reasoning and tool arguments are not displayed.</small>
          </div>
          {live.activity.length > 0 && (
            <details className="eval-workbench__technical"><summary>Tool activity ({live.activity.length} recent events)</summary><ul>{live.activity.map((item, i) => <li key={i}>{item}</li>)}</ul></details>
          )}
          {onStop && <button type="button" className="settings-text-button" onClick={onStop}>Stop evaluation</button>}
        </>
      )}

      {!running && live && outputKeys.length > 0 && (
        <details className="eval-workbench__technical">
          <summary>Review model-generated responses ({outputKeys.length} test runs)</summary>
          <div className="eval-workbench__transcript">
            <label className="eval-workbench__transcript-title">
              Test run
              <select value={historyKey} onChange={(event) => setSelectedCase(event.target.value)}>
                {outputKeys.map((item) => <option key={item} value={item}>{item}</option>)}
              </select>
            </label>
            <div className="eval-workbench__tabs" role="group" aria-label="Model run">
              <button type="button" className={tab === "baseline" ? "is-selected" : ""} onClick={() => setTab("baseline")}>Original approach</button>
              <button type="button" className={tab === "candidate" ? "is-selected" : ""} onClick={() => setTab("candidate")}>New skill</button>
            </div>
            <pre>{live.output[historyKey]?.[tab] || "No generated answer was captured for this run."}</pre>
          </div>
        </details>
      )}

      {report && !running && (
        <div className="eval-workbench__results">
          <div className="eval-workbench__comparison">
            <div><small>Original approach</small><strong>{rate(report, "baseline")}</strong></div>
            <div><small>New skill</small><strong>{rate(report, "candidate")}</strong></div>
            <div><small>Test coverage</small><strong>{report.candidate.graded_cases}/{report.candidate.total_cases}</strong></div>
          </div>
          {report.candidate.skipped_runs > 0 && <p className="eval-workbench__notice">{report.candidate.skipped_runs} run(s) could not be evaluated. This is missing evidence, not a failed task.</p>}
          {!!report.comparison.reasons?.length && (
            <div className="eval-workbench__reasons"><strong>Why this result?</strong><ul>{report.comparison.reasons.map((reason, i) => <li key={i}>{reason}</li>)}</ul></div>
          )}
          <button type="button" className="settings-text-button" aria-expanded={showTechnical} onClick={() => setShowTechnical((value) => !value)}><ChevronDown size={13} /> {showTechnical ? "Hide" : "Show"} technical checks</button>
          {showTechnical && <pre className="eval-workbench__raw">{JSON.stringify({ evaluation_id: report.id, verdict: report.verdict, comparison: report.comparison, baseline: report.baseline, candidate: report.candidate }, null, 2)}</pre>}
        </div>
      )}
      {live?.error && <p className="eval-workbench__notice" role="alert">{live.error}</p>}
      {live?.promoted && <p className="eval-workbench__success">Activated: {live.promoted}</p>}
    </section>
  );
}
