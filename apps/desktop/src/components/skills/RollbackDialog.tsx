import { AlertCircle, RefreshCw, RotateCcw, X } from "lucide-react";

import { useState } from "react";

interface Props {
  skillName: string;
  version: string;
  busy: boolean;
  error?: string | null;
  onConfirm(reason: string): Promise<void> | void;
  onCancel(): void;
}

const DEFAULT_REASON = "Manual rollback from the Skills page.";

export function RollbackDialog({
  skillName,
  version,
  busy,
  error,
  onConfirm,
  onCancel,
}: Props) {
  const [reason, setReason] = useState(DEFAULT_REASON);

  return (
    <div className="skill-confirm-overlay" role="presentation">
      <section
        className="skill-confirm"
        role="dialog"
        aria-modal="true"
        aria-labelledby="rollback-title"
      >
        <header className="skill-confirm__header">
          <div className="skill-confirm__icon">
            <RotateCcw size={18} />
          </div>

          <div className="skill-confirm__title">
            <h2 id="rollback-title">Rollback to v{version}?</h2>

            <p>
              The active bundle for <strong>{skillName}</strong> will be replaced
              by v{version} and re-materialized on disk.
            </p>
          </div>

          <button
            type="button"
            className="settings-icon-button"
            onClick={onCancel}
            aria-label="Close"
            disabled={busy}
          >
            <X size={16} />
          </button>
        </header>

        <div className="skill-confirm__body">
          <label className="skill-confirm__label" htmlFor="rollback-reason">
            Reason
          </label>

          <input
            id="rollback-reason"
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            disabled={busy}
          />

          {error && (
            <div className="settings-error-card">
              <AlertCircle size={16} />

              <span>{error}</span>
            </div>
          )}
        </div>

        <footer className="skill-confirm__footer">
          <button
            type="button"
            className="settings-text-button"
            onClick={onCancel}
            disabled={busy}
          >
            Cancel
          </button>

          <button
            type="button"
            className="settings-text-button settings-text-button--danger"
            onClick={() => void onConfirm(reason)}
            disabled={busy || !reason.trim()}
          >
            {busy ? (
              <RefreshCw className="settings-spin" size={13} />
            ) : (
              <RotateCcw size={13} />
            )}
            Roll back
          </button>
        </footer>
      </section>
    </div>
  );
}
