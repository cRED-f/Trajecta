import {
  AlertCircle,
  CalendarClock,
  Plus,
  RefreshCw,
  Trash2,
} from "lucide-react";

import { useState } from "react";

import {
  useScheduleActions,
  useSchedules,
} from "../../hooks/use-settings";

import type {
  ScheduleCreateInput,
  ScheduleType,
  ScheduledTask,
} from "../../types/settings";

import { SettingsToggle } from "./SettingsToggle";

function formatSchedule(task: ScheduledTask): string {
  if (task.schedule_type === "once") {
    return `Once · ${task.schedule_expr}`;
  }

  if (task.schedule_type === "interval") {
    return `Every ${task.schedule_expr}s`;
  }

  return `Cron · ${task.schedule_expr}`;
}

function ScheduleEditor({
  onClose,
  onCreate,
}: {
  onClose(): void;
  onCreate(body: ScheduleCreateInput): Promise<unknown>;
}) {
  const [name, setName] = useState("");
  const [prompt, setPrompt] = useState("");
  const [scheduleType, setScheduleType] = useState<ScheduleType>("interval");
  const [scheduleExpr, setScheduleExpr] = useState("");
  const [timezone, setTimezone] = useState("UTC");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    if (!name.trim() || !prompt.trim() || !scheduleExpr.trim()) {
      setError("Name, prompt and expression are required.");
      return;
    }

    setSaving(true);
    setError(null);

    try {
      await onCreate({
        name: name.trim(),
        prompt: prompt.trim(),
        schedule_type: scheduleType,
        schedule_expr: scheduleExpr.trim(),
        timezone: timezone.trim() || "UTC",
      });
      onClose();
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "Failed to create schedule.",
      );
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="schedule-editor">
      <div className="schedule-editor__field">
        <label htmlFor="schedule-name">Name</label>
        <input
          id="schedule-name"
          value={name}
          onChange={(event) => setName(event.target.value)}
          placeholder="Weekly cleanup"
        />
      </div>

      <div className="schedule-editor__field">
        <label htmlFor="schedule-prompt">Prompt</label>
        <textarea
          id="schedule-prompt"
          value={prompt}
          onChange={(event) => setPrompt(event.target.value)}
          placeholder="What should the agent do when this runs?"
          rows={3}
        />
      </div>

      <div className="schedule-editor__grid">
        <div className="schedule-editor__field">
          <label htmlFor="schedule-type">Type</label>
          <select
            id="schedule-type"
            value={scheduleType}
            onChange={(event) =>
              setScheduleType(event.target.value as ScheduleType)
            }
          >
            <option value="interval">Interval</option>
            <option value="cron">Cron</option>
            <option value="once">Once</option>
          </select>
        </div>

        <div className="schedule-editor__field">
          <label htmlFor="schedule-expr">Expression</label>
          <input
            id="schedule-expr"
            value={scheduleExpr}
            onChange={(event) => setScheduleExpr(event.target.value)}
            placeholder={
              scheduleType === "interval"
                ? "3600"
                : scheduleType === "cron"
                  ? "0 9 * * 1"
                  : "2026-10-01T09:00:00Z"
            }
          />
        </div>

        <div className="schedule-editor__field">
          <label htmlFor="schedule-timezone">Timezone</label>
          <input
            id="schedule-timezone"
            value={timezone}
            onChange={(event) => setTimezone(event.target.value)}
            placeholder="UTC"
          />
        </div>
      </div>

      {error && (
        <div className="schedule-editor__error">{error}</div>
      )}

      <div className="schedule-editor__actions">
        <button
          type="button"
          className="settings-text-button"
          disabled={saving}
          onClick={onClose}
        >
          Cancel
        </button>

        <button
          type="button"
          className="settings-primary-button"
          disabled={saving}
          onClick={() => void submit()}
        >
          {saving ? (
            <RefreshCw className="settings-spin" size={13} />
          ) : (
            <Plus size={13} />
          )}
          Create schedule
        </button>
      </div>
    </div>
  );
}

interface Props {
  enabled: boolean;
  backendOnline: boolean;
}

export function ScheduledTasksSettings({ enabled, backendOnline }: Props) {
  const [adding, setAdding] = useState(false);

  const query = useSchedules(enabled && backendOnline);
  const actions = useScheduleActions();

  const schedules = query.data?.schedules ?? [];

  return (
    <section>
      <div className="settings-page-header">
        <div>
          <h3>Scheduled Tasks</h3>
          <p>
            Recurring and one-shot autonomous agent jobs.
          </p>
        </div>

        <button
          type="button"
          className="settings-primary-button"
          disabled={!backendOnline || adding}
          onClick={() => setAdding(true)}
        >
          <Plus size={14} />
          New task
        </button>
      </div>

      {!backendOnline ? (
        <div className="settings-empty-state">
          <AlertCircle size={22} />

          <strong>Backend disconnected</strong>

          <span>Connect to the Trajecta backend to manage schedules.</span>
        </div>
      ) : (
        <>
          {adding && (
            <ScheduleEditor
              onClose={() => setAdding(false)}
              onCreate={(body) => actions.create(body)}
            />
          )}

          <div className="schedule-list">
            {query.isLoading && (
              <div className="settings-loading">
                <RefreshCw className="settings-spin" size={17} />
                Loading scheduled tasks…
              </div>
            )}

            {query.isError && (
              <div className="settings-error-card">
                <AlertCircle size={17} />

                <span>
                  {query.error instanceof Error
                    ? query.error.message
                    : "Failed to load scheduled tasks."}
                </span>
              </div>
            )}

            {!query.isLoading &&
              !query.isError &&
              schedules.length === 0 && (
                <div className="settings-empty-state settings-empty-state--small">
                  <CalendarClock size={20} />

                  <strong>No scheduled tasks</strong>

                  <span>
                    Create a task to run autonomous agent work on a schedule.
                  </span>
                </div>
              )}

            {schedules.map((task) => (
              <div className="schedule-row" key={task.id}>
                <div className="schedule-row__icon">
                  <CalendarClock size={16} />
                </div>

                <div className="schedule-row__content">
                  <div className="schedule-row__title">{task.name}</div>
                  <div className="schedule-row__metadata">
                    {formatSchedule(task)}
                    {task.next_run_at
                      ? ` · next ${new Date(task.next_run_at).toLocaleString()}`
                      : ""}
                  </div>
                </div>

                <SettingsToggle
                  checked={task.enabled}
                  disabled={actions.updating}
                  label={`Enable ${task.name}`}
                  onChange={(enabled) =>
                    void actions.update({
                      scheduleId: task.id,
                      body: { enabled },
                    })
                  }
                />

                <button
                  type="button"
                  className="settings-icon-button"
                  aria-label={`Delete ${task.name}`}
                  disabled={actions.removing}
                  onClick={() => void actions.remove(task.id)}
                >
                  <Trash2 size={15} />
                </button>
              </div>
            ))}
          </div>
        </>
      )}
    </section>
  );
}