import { X } from "lucide-react";
import { KnowledgeCenter } from "./KnowledgeCenter";
import { ScheduledTasksSettings } from "./settings";
import type { TopLevelPage } from "../stores/chat-store";

interface Props {
  page: TopLevelPage;
  backendOnline: boolean;
  onClose(): void;
}

export function TopLevelPages({ page, backendOnline, onClose }: Props) {
  // Legacy destinations continue to open the unified Knowledge center.
  const knowledge = page !== "scheduled-tasks";
  return <main className="top-page">
    <button type="button" className="top-page__close" onClick={onClose} aria-label="Close page" title="Back to chat">
      <X size={18} strokeWidth={1.8}/>
    </button>
    <div className="top-page__body">
      {knowledge ? <KnowledgeCenter backendOnline={backendOnline} initialSection={page === "memory" ? "memories" : page === "skills" ? "skills" : "overview"}/>
        : <ScheduledTasksSettings enabled backendOnline={backendOnline} />}
    </div>
  </main>;
}
