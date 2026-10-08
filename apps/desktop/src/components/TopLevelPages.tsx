import { X } from "lucide-react";

import {
  MemorySettings,
  ScheduledTasksSettings,
  SkillsSettings,
} from "./settings";

import type { TopLevelPage } from "../stores/chat-store";

interface Props {
  page: TopLevelPage;
  backendOnline: boolean;
  onClose(): void;
}

export function TopLevelPages({ page, backendOnline, onClose }: Props) {
  return (
    <main className="top-page">
      <header className="top-page__header">
        <div>
          <h2>
            {page === "memory"
              ? "Memory"
              : page === "skills"
                ? "Skills"
                : "Scheduled Tasks"}
          </h2>
          <p>
            {page === "memory"
              ? "Browse durable memories and the memory types behind them."
              : page === "skills"
                ? "Knowledge and reusable skills learned from real conversations."
                : "Recurring and one-shot autonomous agent jobs."}
          </p>
        </div>

        <button
          type="button"
          className="settings-close"
          onClick={onClose}
          aria-label="Close"
        >
          <X size={17} />
        </button>
      </header>

      <div className="top-page__body">
        {page === "memory" && (
          <MemorySettings enabled backendOnline={backendOnline} />
        )}

        {page === "skills" && (
          <SkillsSettings enabled backendOnline={backendOnline} />
        )}

        {page === "scheduled-tasks" && (
          <ScheduledTasksSettings enabled backendOnline={backendOnline} />
        )}
      </div>
    </main>
  );
}