import { openUrl } from "@tauri-apps/plugin-opener";
import { isTauri } from "@tauri-apps/api/core";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, ArrowUpRight, Cpu, Network, RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";

import { chatApi } from "../../lib/api";
import { bifrostDashboardUrl } from "../../lib/bifrost-dashboard";
import { useLlmActions, useLlmCatalog } from "../../hooks/use-settings";

interface Props {
  enabled: boolean;
  backendOnline: boolean;
}

function describe(error: unknown): string {
  return error instanceof Error ? error.message : "Operation failed.";
}

/** Model preferences live in Trajecta; provider administration stays in Bifrost. */
export function LLMProvidersSettings({ enabled, backendOnline }: Props) {
  const queryClient = useQueryClient();
  const catalog = useLlmCatalog(enabled && backendOnline);
  const discovered = useQuery({
    queryKey: ["models"],
    queryFn: chatApi.listModels,
    enabled: enabled && backendOnline,
    staleTime: 30_000,
    retry: false,
  });
  const actions = useLlmActions();
  const [model, setModel] = useState("");
  const [error, setError] = useState<string | null>(null);
  const serverModel = catalog.data?.default_model ?? "";
  const dashboard = bifrostDashboardUrl(catalog.data?.gateway.url ?? "http://127.0.0.1:8080");

  // Refetches should not clear unsaved user edits.
  useEffect(() => {
    setModel(serverModel);
    setError(null);
  }, [serverModel]);

  const modelIds = [...new Set((discovered.data?.models ?? []).map((item) => item.id))];

  async function saveModel() {
    if (!model.trim()) return;
    setError(null);
    try {
      await actions.setDefault({ default_model: model.trim() });
    } catch (cause) {
      setError(describe(cause));
    }
  }

  async function openDashboard() {
    if (!dashboard) return;
    setError(null);
    try {
      if (isTauri()) {
        await openUrl(dashboard);
      } else {
        window.open(dashboard, "_blank", "noopener,noreferrer");
      }
    } catch (cause) {
      setError(`Could not open Bifrost dashboard: ${describe(cause)}`);
    }
  }

  function refresh() {
    void catalog.refetch();
    void queryClient.invalidateQueries({ queryKey: ["models"] });
  }

  return (
    <section>
      <div className="settings-page-header">
        <div>
          <h3>LLM gateway &amp; models</h3>
          <p>Choose models in Trajecta. Manage providers, credentials and routing in Bifrost.</p>
        </div>
      </div>

      {!backendOnline ? (
        <div className="settings-empty-state">
          <AlertCircle size={22} />
          <strong>Backend disconnected</strong>
          <span>Connect the Trajecta backend to see gateway status and models.</span>
          <button type="button" className="settings-secondary-button" onClick={() => void openDashboard()}>
            <ArrowUpRight size={14} /> Open local Bifrost dashboard
          </button>
        </div>
      ) : catalog.isLoading && !catalog.data ? (
        <div className="settings-loading"><RefreshCw size={14} className="settings-spin" /> Loading gateway status…</div>
      ) : !catalog.data ? (
        <div className="settings-error-card"><AlertCircle size={15} />{describe(catalog.error)}</div>
      ) : (
        <>
          <div className="settings-group-card">
            <div className="settings-row">
              <div className="settings-row__icon"><Network size={16} /></div>
              <div className="settings-row__body">
                <strong>Bifrost gateway</strong>
                <span>{catalog.data.gateway.url ?? "Gateway not configured"}</span>
              </div>
              <span className={`settings-status-pill ${catalog.data.gateway.reachable ? "settings-status-pill--online" : "settings-status-pill--offline"}`}>
                {catalog.data.gateway.reachable ? "Connected" : "Unreachable"}
              </span>
              <button type="button" className="settings-icon-button" aria-label="Refresh Bifrost and models" disabled={catalog.isFetching || discovered.isFetching} onClick={refresh}>
                <RefreshCw size={15} className={catalog.isFetching || discovered.isFetching ? "settings-spin" : undefined} />
              </button>
            </div>
            <div className="settings-row">
              <div className="settings-row__icon"><ArrowUpRight size={16} /></div>
              <div className="settings-row__body">
                <strong>Provider administration</strong>
                <span>API keys, endpoints, virtual keys, fallbacks, limits and routing rules</span>
              </div>
              <button type="button" className="settings-secondary-button" disabled={!dashboard} onClick={() => void openDashboard()}>
                <ArrowUpRight size={14} /> Open Bifrost dashboard
              </button>
            </div>
          </div>
          {!catalog.data.gateway.reachable && (
            <div className="settings-error-card"><AlertCircle size={15} />Bifrost is offline. Configure or start the gateway, then refresh to discover models.</div>
          )}

          <div className="settings-subsection">
            <div className="settings-subsection__heading-row">
              <h4 className="settings-subsection__heading">Default model for new conversations</h4>
            </div>
            <div className="settings-group-card">
              <div className="settings-row">
                <div className="settings-row__icon"><Cpu size={16} /></div>
                <div className="settings-row__body">
                  <strong>Model</strong>
                  <span>Existing conversations retain their selection. A bare model ID can use Bifrost model-catalog or routing resolution.</span>
                </div>
                <select
                  aria-label="Discovered Bifrost models"
                  className="llm-default-select"
                  value={modelIds.includes(model) ? model : ""}
                  disabled={actions.savingDefault || !modelIds.length}
                  onChange={(event) => { setModel(event.target.value); setError(null); }}
                >
                  <option value="">{discovered.isFetching ? "Loading models…" : "Choose discovered model…"}</option>
                  {modelIds.map((id) => <option key={id} value={id}>{id}</option>)}
                </select>
              </div>
              <div className="settings-row">
                <div className="settings-row__body">
                  <strong>Model ID or routing alias</strong>
                  <span>Enter an ID defined in Bifrost, including aliases not listed by discovery.</span>
                </div>
                <input
                  aria-label="Default model ID"
                  className="llm-default-model-input"
                  value={model}
                  placeholder="provider/model or gateway model alias"
                  maxLength={200}
                  disabled={actions.savingDefault}
                  onChange={(event) => { setModel(event.target.value); setError(null); }}
                />
                <button
                  type="button"
                  className="settings-primary-button"
                  disabled={!model.trim() || model.trim() === serverModel || actions.savingDefault}
                  onClick={() => void saveModel()}
                >
                  {actions.savingDefault ? "Saving…" : "Save"}
                </button>
              </div>
            </div>
          </div>
          {error && <div className="settings-error-card" role="alert"><AlertCircle size={15} />{error}</div>}
          <p className="llm-note">Trajecta model settings do not store provider credentials. Bifrost controls actual provider selection, permissions, fallback and governance. Provider-free IDs require a Bifrost version that supports model-catalog resolution or a matching routing rule.</p>
        </>
      )}
    </section>
  );
}
