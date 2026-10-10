import {
  AlertCircle,
  Cpu,
  Network,
  Plug,
  Plus,
  RefreshCw,
  Server,
} from "lucide-react";

import { Fragment, useEffect, useState } from "react";

import {
  useLlmActions,
  useLlmCatalog,
  useLlmProviderModels,
} from "../../hooks/use-settings";

import type {
  LlmProviderEntry,
  LlmProviderType,
  LlmTestResult,
} from "../../types/settings";

interface Props {
  enabled: boolean;
  backendOnline: boolean;
}

const TYPE_LABELS: Record<LlmProviderType, string> = {
  openai: "OpenAI",
  anthropic: "Anthropic",
  ollama: "Ollama",
  openai_compat: "OpenAI-compatible",
};

interface ProviderForm {
  id: string;
  type: LlmProviderType;
  baseUrl: string;
  apiKey: string;
  /** True while adding a brand-new provider (id editable). */
  isNew: boolean;
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}

export function LLMProvidersSettings({ enabled, backendOnline }: Props) {
  const catalog = useLlmCatalog(enabled && backendOnline);
  const actions = useLlmActions();

  const data = catalog.data;
  const providers = data?.providers ?? [];

  // ---- Global default (new conversations only) --------------------
  const [defaultProvider, setDefaultProvider] = useState("");
  const [defaultModel, setDefaultModel] = useState("");
  const [defaultError, setDefaultError] = useState<string | null>(null);

  const serverProvider = data?.default_provider ?? "";
  const serverModel = data?.default_model ?? "";

  // Only re-sync when the server's values change, so a background refetch
  // never wipes an in-progress selection.
  useEffect(() => {
    if (!serverProvider) return;
    setDefaultProvider(serverProvider);
    setDefaultModel(serverModel);
    setDefaultError(null);
  }, [serverProvider, serverModel]);

  const dirty =
    Boolean(data) &&
    (defaultProvider !== serverProvider || defaultModel !== serverModel);

  const defaultProviderConfigured =
    providers.find((entry) => entry.id === defaultProvider)?.configured ??
    false;

  const modelsQuery = useLlmProviderModels(
    defaultProvider || null,
    enabled && backendOnline && defaultProviderConfigured,
  );
  const models = modelsQuery.data?.models ?? [];

  // ---- Configure / add provider form --------------------------------
  const [form, setForm] = useState<ProviderForm | null>(null);
  const [formError, setFormError] = useState<string | null>(null);

  const [testResults, setTestResults] = useState<
    Record<string, LlmTestResult>
  >({});

  function openConfigure(entry: LlmProviderEntry) {
    setFormError(null);
    setForm({
      id: entry.id,
      type: entry.type,
      baseUrl: "",
      apiKey: "",
      isNew: false,
    });
  }

  function openAdd() {
    setFormError(null);
    setForm({
      id: "",
      type: "openai_compat",
      baseUrl: "",
      apiKey: "",
      isNew: true,
    });
  }

  function closeForm() {
    setForm(null);
    setFormError(null);
  }

  function patchForm(patch: Partial<ProviderForm>) {
    setForm((current) => (current ? { ...current, ...patch } : current));
  }

  async function saveForm() {
    if (!form) return;

    const provider = form.id.trim();
    const baseUrl = form.baseUrl.trim();

    if (!provider) {
      setFormError("Provider id is required.");
      return;
    }
    if (form.type === "openai_compat" && !baseUrl) {
      setFormError("Base URL is required for OpenAI-compatible providers.");
      return;
    }

    try {
      await actions.upsert({
        provider,
        body: {
          type: form.type,
          base_url: baseUrl || null,
          api_key: form.apiKey.trim() || null,
        },
      });
      closeForm();
    } catch (error) {
      setFormError(errorMessage(error, "Failed to save the provider."));
    }
  }

  async function runTest(provider: string) {
    try {
      const result = await actions.test(provider);
      setTestResults((previous) => ({ ...previous, [provider]: result }));
    } catch (error) {
      setTestResults((previous) => ({
        ...previous,
        [provider]: {
          reachable: false,
          status: "error",
          detail: errorMessage(error, "Test failed."),
        },
      }));
    }
  }

  async function saveDefault() {
    if (!defaultProvider || !defaultModel.trim()) return;

    setDefaultError(null);
    try {
      await actions.setDefault({
        default_provider: defaultProvider,
        default_model: defaultModel.trim(),
      });
    } catch (error) {
      setDefaultError(errorMessage(error, "Failed to save the default."));
    }
  }

  // ---- Render -------------------------------------------------------
  if (!backendOnline) {
    return (
      <section>
        <div className="settings-page-header">
          <div>
            <h3>LLM Providers</h3>
            <p>
              Bifrost is Trajecta&apos;s LLM gateway — providers and models
              are configured behind it.
            </p>
          </div>
        </div>

        <div className="settings-empty-state">
          <AlertCircle size={22} />

          <strong>Backend disconnected</strong>

          <span>Connect to the Trajecta backend to manage providers.</span>
        </div>
      </section>
    );
  }

  if (catalog.isLoading && !data) {
    return (
      <section>
        <div className="settings-page-header">
          <div>
            <h3>LLM Providers</h3>
            <p>
              Bifrost is Trajecta&apos;s LLM gateway — providers and models
              are configured behind it.
            </p>
          </div>
        </div>

        <div className="settings-loading">
          <RefreshCw className="settings-spin" size={14} />
          Loading gateway status…
        </div>
      </section>
    );
  }

  if (!data) {
    return (
      <section>
        <div className="settings-page-header">
          <div>
            <h3>LLM Providers</h3>
            <p>
              Bifrost is Trajecta&apos;s LLM gateway — providers and models
              are configured behind it.
            </p>
          </div>
        </div>

        <div className="settings-error-card">
          <AlertCircle size={15} />
          {errorMessage(catalog.error, "Could not load the LLM catalog.")}
        </div>
      </section>
    );
  }

  const gateway = data.gateway;

  return (
    <section>
      <div className="settings-page-header">
        <div>
          <h3>LLM Providers</h3>
          <p>
            Bifrost is Trajecta&apos;s LLM gateway — providers and models are
            configured behind it.
          </p>
        </div>
      </div>

      <div className="settings-info-banner">
        <Network size={15} />
        <span>
          Bifrost is the gateway, not a selectable option — every chat request
          routes through it. You choose the provider and model behind it.
        </span>
      </div>

      {/* Gateway status */}
      <div className="settings-group-card">
        <div className="settings-row">
          <div className="settings-row__icon">
            <Network size={16} />
          </div>

          <div className="settings-row__body">
            <strong>Bifrost gateway</strong>
            <span>
              {gateway.url ?? "No gateway URL configured"}
              {" · "}
              {gateway.reachable
                ? "Every request routes through /v1"
                : "Not responding"}
            </span>
          </div>

          <span
            className={`settings-status-pill ${
              gateway.reachable
                ? "settings-status-pill--online"
                : "settings-status-pill--offline"
            }`}
          >
            {gateway.reachable ? "Connected" : "Unreachable"}
          </span>

          <button
            type="button"
            className="settings-icon-button"
            aria-label="Refresh gateway status"
            disabled={catalog.isFetching}
            onClick={() => void catalog.refetch()}
          >
            <RefreshCw
              size={15}
              className={catalog.isFetching ? "settings-spin" : undefined}
            />
          </button>
        </div>
      </div>

      {!gateway.reachable && (
        <div className="settings-error-card">
          <AlertCircle size={15} />
          Bifrost is not reachable at {gateway.url ?? "the configured URL"} —
          start the gateway to configure providers.
        </div>
      )}

      {/* Global default */}
      <div className="settings-subsection">
        <div className="settings-subsection__heading-row">
          <h4 className="settings-subsection__heading">
            Default for new conversations
          </h4>
        </div>

        <div className="settings-group-card">
          <div className="settings-row">
            <div className="settings-row__icon">
              <Server size={16} />
            </div>

            <div className="settings-row__body">
              <strong>Default provider</strong>
              <span>
                Existing conversations keep the model they started with.
              </span>
            </div>

            <select
              aria-label="Default provider"
              className="llm-default-select"
              value={defaultProvider}
              disabled={!providers.length || actions.savingDefault}
              onChange={(event) => {
                setDefaultProvider(event.target.value);
                setDefaultModel("");
                setDefaultError(null);
              }}
            >
              {!defaultProvider && (
                <option value="">Select provider…</option>
              )}

              {providers
                .filter((entry) => entry.configured)
                .map((entry) => (
                  <option key={entry.id} value={entry.id}>
                    {entry.id}
                  </option>
                ))}

              {defaultProvider &&
                !providers.some(
                  (entry) =>
                    entry.id === defaultProvider && entry.configured,
                ) && (
                  <option value={defaultProvider}>
                    {defaultProvider} (not configured)
                  </option>
                )}
            </select>
          </div>

          <div className="settings-row">
            <div className="settings-row__icon">
              <Cpu size={16} />
            </div>

            <div className="settings-row__body">
              <strong>Default model</strong>
              <span>
                {!defaultProvider
                  ? "Pick a provider first."
                  : defaultProviderConfigured
                    ? modelsQuery.isLoading || modelsQuery.isFetching
                      ? "Discovering models…"
                      : models.length
                        ? "New conversations start on this model."
                        : "No models discovered — enter one, or run Test connection."
                    : "Configure the provider to discover its models."
                  }
              </span>
            </div>

            {models.length > 0 ? (
              <select
                aria-label="Default model"
                className="llm-default-select"
                value={defaultModel}
                disabled={!defaultProvider || actions.savingDefault}
                onChange={(event) => {
                  setDefaultModel(event.target.value);
                  setDefaultError(null);
                }}
              >
                {!defaultModel && <option value="">Select model…</option>}

                {defaultModel &&
                  !models.includes(defaultModel) && (
                    <option value={defaultModel}>{defaultModel}</option>
                  )}

                {models.map((model) => (
                  <option key={model} value={model}>
                    {model}
                  </option>
                ))}
              </select>
            ) : (
              <input
                aria-label="Default model"
                type="text"
                className="llm-default-model-input"
                placeholder={
                  defaultProvider ? `${defaultProvider}/model-name` : "model-name"
                }
                value={defaultModel}
                disabled={!defaultProvider || actions.savingDefault}
                onChange={(event) => {
                  setDefaultModel(event.target.value);
                  setDefaultError(null);
                }}
              />
            )}

            <button
              type="button"
              className="settings-primary-button"
              disabled={
                !dirty ||
                !defaultProvider ||
                !defaultModel.trim() ||
                actions.savingDefault
              }
              onClick={() => void saveDefault()}
            >
              {actions.savingDefault ? (
                <>
                  <RefreshCw className="settings-spin" size={13} />
                  Saving…
                </>
              ) : (
                "Save"
              )}
            </button>
          </div>
        </div>

        {defaultError && (
          <div className="settings-error-card">
            <AlertCircle size={15} />
            {defaultError}
          </div>
        )}
      </div>

      {/* Providers */}
      <div className="settings-subsection">
        <div className="settings-subsection__heading-row llm-providers__heading-row">
          <h4 className="settings-subsection__heading">Providers</h4>

          <button
            type="button"
            className="settings-secondary-button"
            onClick={openAdd}
            disabled={actions.upserting}
          >
            <Plus size={14} />
            Add provider
          </button>
        </div>

        <div className="settings-group-card">
          {providers.map((entry) => {
            const testing = actions.testingProvider === entry.id;
            const result = testResults[entry.id];

            return (
              <Fragment key={entry.id}>
                <div className="settings-row llm-provider-row">
                  <div className="settings-row__icon">
                    <Plug size={16} />
                  </div>

                  <div className="settings-row__body">
                    <strong>{entry.id}</strong>
                    <span>{TYPE_LABELS[entry.type] ?? entry.type}</span>
                  </div>

                  <span
                    className={`settings-status-pill ${
                      !entry.configured
                        ? "llm-status-pill--idle"
                        : entry.reachable
                          ? "settings-status-pill--online"
                          : "settings-status-pill--offline"
                    }`}
                  >
                    {!entry.configured
                      ? "Not configured"
                      : entry.reachable
                        ? "Ready"
                        : "Key failed"}
                  </span>

                  {testing && (
                    <span className="llm-provider-row__test">
                      <RefreshCw className="settings-spin" size={13} />
                      Testing…
                    </span>
                  )}

                  {!testing && result && (
                    <span
                      className={`llm-provider-row__test ${
                        result.reachable
                          ? "llm-provider-row__test--ok"
                          : "llm-provider-row__test--fail"
                      }`}
                      title={result.detail ?? undefined}
                    >
                      {result.reachable
                        ? "Reachable"
                        : result.detail || "Unreachable"}
                    </span>
                  )}

                  {entry.configured && (
                    <button
                      type="button"
                      className="settings-text-button"
                      disabled={actions.testingProvider !== null}
                      onClick={() => void runTest(entry.id)}
                    >
                      Test connection
                    </button>
                  )}

                  <button
                    type="button"
                    className="settings-secondary-button"
                    onClick={() => openConfigure(entry)}
                    disabled={actions.upserting}
                  >
                    Configure
                  </button>
                </div>

                {form && !form.isNew && form.id === entry.id && (
                  <ProviderFormFields
                    form={form}
                    error={formError}
                    saving={actions.upserting}
                    onChange={patchForm}
                    onCancel={closeForm}
                    onSave={() => void saveForm()}
                  />
                )}
              </Fragment>
            );
          })}
        </div>

        {form?.isNew && (
          <div className="llm-form-card">
            <ProviderFormFields
              form={form}
              error={formError}
              saving={actions.upserting}
              onChange={patchForm}
              onCancel={closeForm}
              onSave={() => void saveForm()}
            />
          </div>
        )}

        <p className="llm-note">
          API keys are stored in Bifrost only — Trajecta never persists them in
          its own database.
        </p>
      </div>
    </section>
  );
}

interface FormProps {
  form: ProviderForm;
  error: string | null;
  saving: boolean;
  onChange(patch: Partial<ProviderForm>): void;
  onCancel(): void;
  onSave(): void;
}

function ProviderFormFields({
  form,
  error,
  saving,
  onChange,
  onCancel,
  onSave,
}: FormProps) {
  const showBaseUrl =
    form.type === "openai_compat" || form.type === "ollama";
  const showApiKey = form.type !== "ollama";

  return (
    <div className="llm-form">
      <div className="llm-form__grid">
        {form.isNew && (
          <div className="llm-form__field">
            <label htmlFor="llm-provider-id">Provider id</label>
            <input
              id="llm-provider-id"
              type="text"
              placeholder="my-relay"
              value={form.id}
              disabled={saving}
              onChange={(event) => onChange({ id: event.target.value })}
            />
          </div>
        )}

        <div className="llm-form__field">
          <label htmlFor="llm-provider-type">Type</label>
          <input
            id="llm-provider-type"
            type="text"
            value={TYPE_LABELS[form.type]}
            disabled
          />
        </div>

        {showBaseUrl && (
          <div className="llm-form__field">
            <label htmlFor="llm-provider-url">
              Base URL
              {form.type === "openai_compat" ? "" : " (optional)"}
            </label>
            <input
              id="llm-provider-url"
              type="text"
              placeholder={
                form.type === "ollama"
                  ? "http://127.0.0.1:11434"
                  : "https://api.example.com/v1"
              }
              value={form.baseUrl}
              disabled={saving}
              onChange={(event) => onChange({ baseUrl: event.target.value })}
            />
          </div>
        )}

        {showApiKey && (
          <div className="llm-form__field">
            <label htmlFor="llm-provider-key">API key (optional)</label>
            <input
              id="llm-provider-key"
              type="password"
              placeholder={
                form.isNew ? "sk-…" : "Leave blank to keep the stored key"
              }
              value={form.apiKey}
              disabled={saving}
              onChange={(event) => onChange({ apiKey: event.target.value })}
            />
          </div>
        )}
      </div>

      <p className="llm-form__note">
        {form.type === "ollama"
          ? "Ollama runs locally — no API key needed. Default: http://127.0.0.1:11434."
          : "The key is forwarded to Bifrost and never stored in Trajecta."}
        {!form.isNew && showBaseUrl && form.type === "openai_compat"
          ? " The base URL replaces the stored one."
          : ""}
      </p>

      {error && (
        <p className="llm-form__error">
          <AlertCircle size={13} />
          {error}
        </p>
      )}

      <div className="llm-form__actions">
        <button
          type="button"
          className="settings-secondary-button"
          disabled={saving}
          onClick={onCancel}
        >
          Cancel
        </button>

        <button
          type="button"
          className="settings-primary-button"
          disabled={saving || !form.id.trim()}
          onClick={onSave}
        >
          {saving ? (
            <>
              <RefreshCw className="settings-spin" size={13} />
              Saving…
            </>
          ) : (
            "Save provider"
          )}
        </button>
      </div>
    </div>
  );
}
