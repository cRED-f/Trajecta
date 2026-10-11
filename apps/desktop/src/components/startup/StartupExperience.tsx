import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { invoke, isTauri } from "@tauri-apps/api/core";
import { AlertTriangle, Check, Circle, LoaderCircle, X } from "lucide-react";

import { chatApi, request, settingsApi } from "../../lib/api";
import { learningMemoryApi } from "../../lib/learning-memory-api";
import { queryKeys } from "../../hooks/use-chat";
import { settingsQueryKeys } from "../../hooks/use-settings";
import { useChatStore } from "../../stores/chat-store";
import type { Conversation } from "../../types/chat";
import type { EmbeddingCatalog, LlmCatalog } from "../../types/settings";
import type { ReflectionSettings } from "../../lib/learning-memory-api";
import {
  chooseOllamaNotice, initialStartupProgress, startupSteps,
  type DesktopStartupStatus, type StartupNotice, type StartupProgress,
  type StartupStep, type StepState, type WarmupResult,
} from "../../lib/startup";

const managedDesktop = isTauri();
const pause = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));
const errorText = (error: unknown) => error instanceof Error ? error.message : String(error);
// Some provider APIs can stall during local downloads or provider discovery.
// Optional startup steps must always have a finite wait time.
function bounded<T>(promise: Promise<T>, milliseconds = 22_000): Promise<T> {
  let timeout: ReturnType<typeof setTimeout> | undefined;
  return Promise.race([
    promise,
    new Promise<never>((_, reject) => {
      timeout = setTimeout(() => reject(new Error("Startup check timed out; it can be retried in Settings")), milliseconds);
    }),
  ]).finally(() => { if (timeout !== undefined) clearTimeout(timeout); });
}

export function useStartupExperience() {
  const client = useQueryClient();
  const [ready, setReady] = useState(!managedDesktop);
  const [attempt, setAttempt] = useState(0);
  const [progress, setProgress] = useState<StartupProgress>(initialStartupProgress);
  const [status, setStatus] = useState("Preparing local services…");
  const [failure, setFailure] = useState<string | null>(null);
  const [notices, setNotices] = useState<StartupNotice[]>([]);

  useEffect(() => {
    if (!managedDesktop || ready) return;
    let cancelled = false;
    const step = (name: StartupStep, state: StepState) => {
      if (!cancelled) setProgress((previous) => ({ ...previous, [name]: state }));
    };
    const note = (value: StartupNotice) => {
      if (!cancelled) setNotices((all) => all.some((item) => item.id === value.id) ? all : [...all, value]);
    };
    async function restore() {
      setFailure(null);
      setProgress(initialStartupProgress());
      setStatus("Waiting for FastAPI to initialize…");
      step("fastapi", "running");
      step("bifrost", "running");

      // Tauri owns launching and supervising both processes. Never run
      // duplicate spawn commands from the React WebView.
      let service: DesktopStartupStatus | null = null;
      const deadline = Date.now() + 72_000;
      while (!cancelled && Date.now() < deadline) {
        try {
          service = await invoke<DesktopStartupStatus>("desktop_status");
        } catch (error) {
          if (Date.now() + 1200 >= deadline) throw error;
          await pause(850);
          continue;
        }
        if (service.backend_state === "connected") break;
        if (service.backend_state === "failed") {
          throw new Error(service.error ?? "FastAPI failed to start. See Settings → Backend services.");
        }
        await pause(850);
      }
      if (cancelled) return;
      if (service?.backend_state !== "connected") {
        throw new Error(service?.error ?? "FastAPI did not become ready. Check the backend log in Settings → Desktop & Application.");
      }
      step("fastapi", "ready");
      setStatus("Restoring your workspace…");

      // Startup reads are parallel; an Ollama or Bifrost error must not make
      // saved conversations and settings inaccessible.
      const load = async (id: StartupStep, work: () => Promise<void>) => {
        step(id, "running");
        try { await bounded(work()); step(id, "ready"); }
        catch (error) {
          step(id, "warning");
          note({ id, kind: "warning", message: `Could not restore ${id}: ${errorText(error)}` });
        }
      };
      await Promise.all([
        load("settings", async () => {
          const results = await Promise.allSettled([
            client.fetchQuery({ queryKey: settingsQueryKeys.llm, queryFn: settingsApi.llmCatalog }),
            client.fetchQuery({ queryKey: settingsQueryKeys.permissions, queryFn: settingsApi.permissions }),
            client.fetchQuery({ queryKey: settingsQueryKeys.guardrails, queryFn: settingsApi.guardrails }),
            client.fetchQuery({ queryKey: settingsQueryKeys.memory(""), queryFn: () => settingsApi.memory("") }),
            client.fetchQuery({ queryKey: queryKeys.models, queryFn: chatApi.listModels }),
            client.fetchQuery({ queryKey: settingsQueryKeys.schedules, queryFn: settingsApi.schedules }),
          ]);
          if (results.some((result) => result.status === "rejected")) {
            throw new Error("Some provider or application settings are temporarily unavailable");
          }
        }),
        load("conversations", async () => {
          const conversations: Conversation[] = await client.fetchQuery({
            queryKey: queryKeys.conversations, queryFn: chatApi.listConversations,
          });
          // Sidebar gets the full DB list. Prime a few recent threads for
          // instant navigation without loading every message into RAM.
          const savedActive = useChatStore.getState().activeConversationId;
          const ids = [...new Set([
            ...(savedActive && conversations.some((c) => c.id === savedActive) ? [savedActive] : []),
            ...conversations.slice(0, 3).map((c) => c.id),
          ])];
          await Promise.allSettled(ids.map((id) => client.fetchQuery({
            queryKey: queryKeys.conversation(id), queryFn: () => chatApi.getConversation(id),
          })));
        }),
        load("learning", async () => {
          const [config, worker] = await Promise.all([
            client.fetchQuery({ queryKey: ["memory-center", "reflection-settings"], queryFn: learningMemoryApi.reflectionSettings }),
            client.fetchQuery({ queryKey: ["memory-center", "reflection"], queryFn: learningMemoryApi.reflection }),
          ]);
          if (config.enabled && !worker.enabled) {
            throw new Error("The saved learning model is selected, but the worker is not enabled");
          }
        }),
        load("embedding", async () => {
          await client.fetchQuery({
            queryKey: settingsQueryKeys.embedding, queryFn: settingsApi.embedding,
          });
        }),
      ]);
      if (cancelled) return;

      setStatus("Checking local model availability…");
      let warmup: WarmupResult | null = null;
      try {
        // Does NOT call remote/paid models. FastAPI only warms explicitly
        // configured local Ollama models, if they are available.
        warmup = await bounded(request<WarmupResult>("/startup/warmup", { method: "POST" }), 21_000);
      } catch (error) {
        note({ id: "model-warmup", kind: "warning", message: `Local model warmup was skipped: ${errorText(error)}` });
      }
      if (cancelled) return;
      const reflection = client.getQueryData<ReflectionSettings>(["memory-center", "reflection-settings"]);
      const defaultModel = client.getQueryData<LlmCatalog>(settingsQueryKeys.llm)?.default_model ?? null;
      // When the reflection model is blank it inherits the configured default.
      const activeLearning = reflection?.enabled ? (reflection.model ?? defaultModel) : null;
      const embedding = client.getQueryData<EmbeddingCatalog>(settingsQueryKeys.embedding) ?? null;
      const localNotice = chooseOllamaNotice(embedding, activeLearning, warmup);
      if (localNotice) note(localNotice);
      if (warmup?.embedding.state === "unavailable") step("embedding", "warning");
      if (warmup?.learning.state === "unavailable") step("learning", "warning");

      // Bifrost can still be downloading/launching after FastAPI is ready.
      // Allow a short independent grace period but never block offline work.
      const gatewayDeadline = Date.now() + 5_000;
      while (!cancelled && service && !service.bifrost_reachable && Date.now() < gatewayDeadline) {
        await pause(650);
        try { service = await invoke<DesktopStartupStatus>("desktop_status"); }
        catch { break; }
      }
      if (cancelled) return;
      if (service?.bifrost_reachable) step("bifrost", "ready");
      else {
        step("bifrost", "warning");
        note({ id: "bifrost", kind: "warning", message: "Bifrost is not reachable yet. Chats using gateway models may fail until it starts. Check Desktop settings." });
      }
      setStatus("Workspace restored");
      setReady(true);
    }
    void restore().catch((error) => {
      if (cancelled) return;
      step("fastapi", "failed");
      setFailure(errorText(error));
      setStatus("Startup needs attention");
    });
    return () => { cancelled = true; };
  }, [client, ready, attempt]);

  function proceedOffline() {
    setReady(true);
    useChatStore.getState().setShowsSettings(true);
  }
  return {
    ready, progress, status, failure, notices,
    retry: () => setAttempt((count) => count + 1),
    dismiss: (id: string) => setNotices((all) => all.filter((notice) => notice.id !== id)),
    proceedOffline,
  };
}

export type StartupExperience = ReturnType<typeof useStartupExperience>;

export function StartupDialog({ startup }: { startup: StartupExperience }) {
  const completed = startupSteps.filter(({ id }) =>
    startup.progress[id] === "ready" || startup.progress[id] === "warning").length;
  return <main className="startup-page">
    <section role="dialog" aria-modal="true" aria-label="Starting Trajecta" className="startup-dialog">
      <div className="startup-logo"><img src="/icon.svg" alt="" width="40" height="40" /></div>
      <h1>Starting Trajecta</h1>
      <p className="startup-description">Restoring your local workspace and AI services.</p>
      <div className="startup-progress" role="progressbar" aria-label="Startup progress"
        aria-valuemin={0} aria-valuemax={startupSteps.length} aria-valuenow={completed}>
        <span style={{ width: `${(completed / startupSteps.length) * 100}%` }}/>
      </div>
      <div className="startup-steps">
        {startupSteps.map(({ id, title }) => <div key={id} className={`startup-step startup-step--${startup.progress[id]}`}>
          {startup.progress[id] === "running" ? <LoaderCircle className="startup-spin" size={17}/>
            : startup.progress[id] === "ready" ? <Check size={17}/>
            : startup.progress[id] === "warning" || startup.progress[id] === "failed" ? <AlertTriangle size={17}/>
            : <Circle size={15}/>}
          <span>{title}</span>
          <small>{startup.progress[id] === "ready" ? "Ready" : startup.progress[id] === "warning" ? "Limited" : startup.progress[id] === "failed" ? "Failed" : startup.progress[id] === "running" ? "Loading" : "Pending"}</small>
        </div>)}
      </div>
      {startup.failure ? <div role="alert" className="startup-failure">{startup.failure}</div>
        : <p className="startup-current" role="status" aria-live="polite">{startup.status}</p>}
      {startup.failure && <div className="startup-actions">
        <button type="button" onClick={startup.retry}>Retry startup</button>
        <button type="button" className="startup-secondary" onClick={startup.proceedOffline}>Open Settings</button>
      </div>}
    </section>
  </main>;
}

export function StartupToasts({ startup }: { startup: StartupExperience }) {
  if (!startup.ready || !startup.notices.length) return null;
  return <div className="startup-toast-stack" aria-live="polite">
    {startup.notices.map((notice) => <div className="startup-toast" key={notice.id} role="status">
      <AlertTriangle size={18}/><p>{notice.message}</p>
      <button type="button" title="Dismiss notice" aria-label="Dismiss notice"
        onClick={() => startup.dismiss(notice.id)}><X size={16}/></button>
    </div>)}
  </div>;
}
