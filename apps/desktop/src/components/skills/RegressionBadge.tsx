import { ShieldCheck, TrendingDown } from "lucide-react";

import type { SkillEvaluationReport } from "../../types/settings";

/**
 * Same two rules the backend SkillRegressionDetector applies: a lower
 * success rate or more tool errors than the baseline means regression.
 */
export function evaluationRegression(
  report: SkillEvaluationReport,
): boolean {
  return (
    report.candidate.success_rate < report.baseline.success_rate ||
    report.candidate.tool_errors > report.baseline.tool_errors
  );
}

interface Props {
  regression: boolean;
}

export function RegressionBadge({ regression }: Props) {
  return (
    <span
      className={`skill-regression skill-regression--${
        regression ? "bad" : "ok"
      }`}
    >
      {regression ? <TrendingDown size={12} /> : <ShieldCheck size={12} />}

      {regression ? "Regression detected" : "Healthy"}
    </span>
  );
}
