import type { ReactNode } from "react";
import { ArrowUpRight, CheckCircle2, Info, type LucideIcon } from "lucide-react";

export function DashboardMetric({ label, value, description, icon: Icon, onClick, attention = false }: {
  label: string;
  value: number | string;
  description: string;
  icon: LucideIcon;
  onClick?: () => void;
  attention?: boolean;
}) {
  const content = <>
    <div className="knowledge-metric__top"><span>{label}</span><Icon size={18} aria-hidden="true" /></div>
    <strong className="knowledge-metric__value">{value}</strong>
    <span className="knowledge-metric__description">{description}</span>
    {onClick && <ArrowUpRight className="knowledge-metric__arrow" size={16} aria-hidden="true" />}
  </>;
  return onClick ? (
    <button className={`knowledge-metric knowledge-metric--interactive${attention ? " knowledge-metric--attention" : ""}`} type="button" onClick={onClick}>
      {content}
    </button>
  ) : (
    <div className="knowledge-metric">{content}</div>
  );
}

export function StatusPill({ status, children }: { status: string; children?: ReactNode }) {
  const type = status === "active" || status === "approved" || status === "completed" || status === "promoted" ? "success"
    : status === "needs_review" || status === "pending" || status === "queued" || status === "candidate" ? "warning"
      : status === "rejected" || status === "failed" ? "danger" : "neutral";
  return <span className={`knowledge-status knowledge-status--${type}`}>{children ?? status.replaceAll("_", " ")}</span>;
}

export function DashboardSection({ title, description, action, children }: {
  title: string; description?: string; action?: ReactNode; children: ReactNode;
}) {
  return <section className="knowledge-section">
    <div className="knowledge-section__header"><div><h4>{title}</h4>{description && <p>{description}</p>}</div>{action}</div>
    {children}
  </section>;
}

export function LearningFlow({ variant }: { variant: "memory" | "skill" }) {
  const stages = variant === "memory" ? [
    ["Observe", "Trajecta records useful facts or task history."],
    ["Reflect", "Background reviews may identify patterns."],
    ["Consolidate", "Related turns become one task experience automatically."],
  ] : [
    ["Observe", "Capture multi-turn tasks and verified evidence."],
    ["Evaluate", "Test reusable workflows without interrupting the chat."],
    ["Activate", "Only skills passing safety and quality gates become active."],
  ];
  return <section className="knowledge-flow" aria-label="How learning works">
    <div className="knowledge-flow__heading"><Info size={16} aria-hidden="true" /><strong>How {variant === "memory" ? "memory" : "skill learning"} works</strong></div>
    <ol>{stages.map(([title, description], index) => <li key={title}>
      <span className="knowledge-flow__number">{index + 1}</span>
      <div><strong>{title}</strong><p>{description}</p></div>
    </li>)}</ol>
    <p className="knowledge-flow__footnote"><CheckCircle2 size={14} aria-hidden="true" /> Learning is automatic; tool permissions remain enforced.</p>
  </section>;
}

export function FriendlyEmpty({ icon: Icon, title, description, action }: {
  icon: LucideIcon; title: string; description: string; action?: ReactNode;
}) {
  return <div className="knowledge-empty"><div className="knowledge-empty__icon"><Icon size={23} aria-hidden="true" /></div>
    <strong>{title}</strong><p>{description}</p>{action}</div>;
}
