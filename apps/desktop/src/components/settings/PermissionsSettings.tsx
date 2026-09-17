import { AlertCircle, MonitorCheck, RefreshCw, ShieldCheck } from "lucide-react";

import { usePermissionActions, usePermissions } from "../../hooks/use-settings";

import type { PermissionItem, PermissionMode } from "../../types/settings";

const MODES: { value: PermissionMode; label: string }[] = [
  { value: "allow", label: "Allow" },
  { value: "ask", label: "Ask" },
  { value: "deny", label: "Deny" },
];

function PermissionSelector({
  item,
  disabled,
  pending,
  onSelect,
}: {
  item: PermissionItem;
  disabled: boolean;
  pending: boolean;
  onSelect(mode: PermissionMode): void;
}) {
  return (
    <div
      className="permission-selector"
      role="group"
      aria-label={`${item.title} mode`}
    >
      {MODES.map(({ value, label }) => (
        <button
          type="button"
          key={value}
          className={`permission-selector__option permission-selector__option--${value} ${
            item.mode === value ? "permission-selector__option--active" : ""
          }`}
          aria-pressed={item.mode === value}
          disabled={disabled}
          onClick={() => onSelect(value)}
        >
          {label}
        </button>
      ))}

      {pending && (
        <RefreshCw className="settings-spin permission-selector__spinner" size={14} />
      )}
    </div>
  );
}

export function PermissionsSettings({
  enabled,
  backendOnline,
}: {
  enabled: boolean;
  backendOnline: boolean;
}) {
  const query = usePermissions(enabled && backendOnline);
  const actions = usePermissionActions();

  const items = query.data?.permissions ?? [];

  return (
    <section>
      <div className="settings-page-header">
        <div>
          <h3>Permissions</h3>
          <p>
            Decide how Trajecta uses each capability. Deny hides the tool,
            Ask pauses for approval, Allow runs freely.
          </p>
        </div>
      </div>

      {!backendOnline ? (
        <div className="settings-empty-state">
          <AlertCircle size={22} />

          <strong>Backend disconnected</strong>

          <span>Connect to the Trajecta backend to manage permissions.</span>
        </div>
      ) : (
        <>
          <div className="settings-info-banner">
            <ShieldCheck size={15} />
            <span>
              A denied permission removes the tools from the next agent run. A
              same-run tool receives no change until the next run starts.
            </span>
          </div>

          {query.isLoading && (
            <div className="settings-loading">
              <RefreshCw className="settings-spin" size={17} />
              Loading permissions…
            </div>
          )}

          {query.isError && (
            <div className="settings-error-card">
              <AlertCircle size={17} />

              <span>
                {query.error instanceof Error
                  ? query.error.message
                  : "Failed to load permissions."}
              </span>
            </div>
          )}

          {!query.isLoading && !query.isError && (
            <div className="permission-list">
              {items.map((item) => (
                <div
                  className="permission-row"
                  key={item.id}
                >
                  <div className="permission-row__icon">
                    <MonitorCheck size={16} />
                  </div>

                  <div className="permission-row__content">
                    <strong>{item.title}</strong>

                    <span>{item.description}</span>
                  </div>

                  <PermissionSelector
                    item={item}
                    disabled={actions.updating}
                    pending={
                      actions.updating &&
                      actions.pendingPermissionId === item.id
                    }
                    onSelect={(mode) =>
                      void actions.setMode({
                        permissionId: item.id,
                        mode,
                      })
                    }
                  />
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </section>
  );
}