import {
  AlertCircle,
  Cloud,
  FileWarning,
  KeyRound,
  RefreshCw,
  ShieldAlert,
  UserRoundSearch,
} from "lucide-react";

import type { ReactNode } from "react";

import { useGuardrailActions, useGuardrails } from "../../hooks/use-settings";

import type {
  ContentGuardrailSettings,
  GuardrailPromptAction,
  GuardrailSensitiveAction,
} from "../../types/settings";

import { SettingsToggle } from "./SettingsToggle";

function ToggleRow({
  icon,
  title,
  description,
  checked,
  disabled,
  onChange,
}: {
  icon: ReactNode;
  title: string;
  description: string;
  checked: boolean;
  disabled: boolean;
  onChange(value: boolean): void;
}) {
  return (
    <div className="settings-row">
      <div className="settings-row__icon">{icon}</div>

      <div className="settings-row__body">
        <strong>{title}</strong>
        <span>{description}</span>
      </div>

      <SettingsToggle
        checked={checked}
        disabled={disabled}
        label={title}
        onChange={onChange}
      />
    </div>
  );
}

export function GuardrailsSettings({
  enabled,
  backendOnline,
}: {
  enabled: boolean;
  backendOnline: boolean;
}) {
  const query = useGuardrails(enabled && backendOnline);

  const actions = useGuardrailActions();

  const settings = query.data?.content;

  function patch(value: Partial<ContentGuardrailSettings>) {
    void actions.update(value);
  }

  return (
    <section>
      <div className="settings-page-header">
        <div>
          <h3>Guardrails</h3>

          <p>Filter prompt injection and sensitive data at model boundaries.</p>
        </div>
      </div>

      {!backendOnline ? (
        <div className="settings-empty-state">
          <AlertCircle size={22} />

          <strong>Backend disconnected</strong>

          <span>Connect to Trajecta to configure content guardrails.</span>
        </div>
      ) : query.isLoading ? (
        <div className="settings-loading">
          <RefreshCw className="settings-spin" size={17} />

          Loading guardrails…
        </div>
      ) : query.isError ? (
        <div className="settings-error-card">
          <AlertCircle size={17} />

          <span>
            {query.error instanceof Error
              ? query.error.message
              : "Failed to load guardrails."}
          </span>
        </div>
      ) : !settings ? (
        <div className="settings-error-card">
          <AlertCircle size={17} />

          <span>Guardrail settings were not returned by the backend.</span>
        </div>
      ) : (
        <>
          <div className="settings-info-banner">
            <ShieldAlert size={15} />

            <span>
              Guardrails filter text only. They never authorize filesystem,
              shell, Git, browser, MCP, or other tool actions. Permissions
              remains the ALLOW / ASK / DENY authority.
            </span>
          </div>

          <div className="settings-group-card">
            <ToggleRow
              icon={<ShieldAlert size={16} />}
              title="Content guardrails"
              description={
                "Master switch for prompt, privacy, and output validation."
              }
              checked={settings.enabled}
              disabled={actions.updating}
              onChange={(value) => patch({ enabled: value })}
            />

            <ToggleRow
              icon={<FileWarning size={16} />}
              title="Prompt injection"
              description={
                "Warn on user prompts and inspect untrusted tool, file, web, " +
                "and RAG text."
              }
              checked={settings.prompt_injection_enabled}
              disabled={actions.updating || !settings.enabled}
              onChange={(value) => patch({ prompt_injection_enabled: value })}
            />

            <ToggleRow
              icon={<KeyRound size={16} />}
              title="Secrets"
              description={
                "Redact or block credentials before cloud calls and block " +
                "leaked secrets in assistant output."
              }
              checked={settings.secrets_enabled}
              disabled={actions.updating || !settings.enabled}
              onChange={(value) => patch({ secrets_enabled: value })}
            />

            <ToggleRow
              icon={<UserRoundSearch size={16} />}
              title="PII detection"
              description={
                "Detect configured personal-data entities before cloud-model " +
                "calls."
              }
              checked={settings.pii_enabled}
              disabled={actions.updating || !settings.enabled}
              onChange={(value) => patch({ pii_enabled: value })}
            />

            <ToggleRow
              icon={<ShieldAlert size={16} />}
              title="System prompt leakage"
              description={
                "Hard-block assistant responses that reproduce Trajecta's " +
                "protected prompt."
              }
              checked={settings.system_prompt_leakage_enabled}
              disabled={actions.updating || !settings.enabled}
              onChange={(value) =>
                patch({ system_prompt_leakage_enabled: value })
              }
            />

            <ToggleRow
              icon={<ShieldAlert size={16} />}
              title="Jailbreak detector"
              description={
                "Optional heavier local classifier. Disabled by default."
              }
              checked={settings.jailbreak_enabled}
              disabled={actions.updating || !settings.enabled}
              onChange={(value) => patch({ jailbreak_enabled: value })}
            />
          </div>

          <div className="settings-subsection">
            <h4 className="settings-subsection__heading">Boundary actions</h4>

            <div className="settings-group-card">
              <div className="settings-row">
                <div className="settings-row__icon">
                  <ShieldAlert size={16} />
                </div>

                <div className="settings-row__body">
                  <strong>User prompt detection</strong>

                  <span>
                    Warn is recommended so security research prompts are not
                    blindly rejected.
                  </span>
                </div>

                <select
                  className="guardrail-select"
                  aria-label="User prompt guardrail action"
                  value={settings.user_prompt_action}
                  disabled={actions.updating || !settings.enabled}
                  onChange={(event) =>
                    patch({
                      user_prompt_action:
                        event.target.value as GuardrailPromptAction,
                    })
                  }
                >
                  <option value="warn">Warn</option>

                  <option value="block">Block</option>
                </select>
              </div>

              <div className="settings-row">
                <div className="settings-row__icon">
                  <FileWarning size={16} />
                </div>

                <div className="settings-row__body">
                  <strong>Untrusted content injection</strong>

                  <span>
                    Block replaces suspicious retrieved or tool text before the
                    model sees it.
                  </span>
                </div>

                <select
                  className="guardrail-select"
                  aria-label="Untrusted content guardrail action"
                  value={settings.untrusted_content_action}
                  disabled={actions.updating || !settings.enabled}
                  onChange={(event) =>
                    patch({
                      untrusted_content_action:
                        event.target.value as GuardrailPromptAction,
                    })
                  }
                >
                  <option value="warn">Warn</option>

                  <option value="block">Block</option>
                </select>
              </div>

              <div className="settings-row">
                <div className="settings-row__icon">
                  <Cloud size={16} />
                </div>

                <div className="settings-row__body">
                  <strong>Cloud sensitive data</strong>

                  <span>
                    Ollama is local by default; other providers cross the
                    privacy boundary.
                  </span>
                </div>

                <select
                  className="guardrail-select"
                  aria-label="Cloud sensitive data action"
                  value={settings.cloud_sensitive_action}
                  disabled={actions.updating || !settings.enabled}
                  onChange={(event) =>
                    patch({
                      cloud_sensitive_action:
                        event.target.value as GuardrailSensitiveAction,
                    })
                  }
                >
                  <option value="redact">Redact</option>

                  <option value="block">Block</option>

                  <option value="allow">Allow</option>
                </select>
              </div>
            </div>
          </div>

          {actions.error instanceof Error && (
            <div className="settings-error-card">
              <AlertCircle size={17} />

              <span>{actions.error.message}</span>
            </div>
          )}
        </>
      )}
    </section>
  );
}
