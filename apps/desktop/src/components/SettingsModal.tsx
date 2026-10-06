import { useEffect, useRef, useState } from "react";

import {
  EmbeddingSettings,
  GeneralSettings,
  GuardrailsSettings,
  McpToolsSettings,
  PermissionsSettings,
  SettingsHeader,
  SettingsShell,
} from "./settings";

import { useMcpCatalog } from "../hooks/use-tools";

import type { SettingsPage, ThemeValue } from "./settings/settings-types";

interface Props {
  open: boolean;
  theme: ThemeValue;
  backendOnline: boolean;
  onClose(): void;
  onSetTheme(theme: ThemeValue): void;
}

export function SettingsModal({
  open,
  theme,
  backendOnline,
  onClose,
  onSetTheme,
}: Props) {
  const overlayRef = useRef<HTMLDivElement>(null);

  const [page, setPage] = useState<SettingsPage>("general");

  const mcpQuery = useMcpCatalog(open && backendOnline);

  useEffect(() => {
    if (!open) return;

    function onKeydown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
      }
    }

    window.addEventListener("keydown", onKeydown);

    return () => window.removeEventListener("keydown", onKeydown);
  }, [open, onClose]);

  if (!open) {
    return null;
  }

  return (
    <div
      className="settings-overlay"
      ref={overlayRef}
      onClick={(event) => {
        if (event.target === overlayRef.current) {
          onClose();
        }
      }}
    >
      <div className="settings-modal settings-modal--large">
        <SettingsHeader
          title="Settings"
          subtitle="Configure Trajecta and connected tools."
          onClose={onClose}
        />

        <SettingsShell
          page={page}
          onPageChange={setPage}
          onClose={onClose}
          mcpEnabledCount={mcpQuery.data?.enabled_count}
        >
          {page === "general" && (
            <GeneralSettings
              theme={theme}
              backendOnline={backendOnline}
              onSetTheme={onSetTheme}
            />
          )}

          {page === "memory" && (
            <EmbeddingSettings enabled={open} backendOnline={backendOnline} />
          )}

          {page === "guardrails" && (
            <GuardrailsSettings enabled={open} backendOnline={backendOnline} />
          )}

          {page === "permissions" && (
            <PermissionsSettings
              enabled={open}
              backendOnline={backendOnline}
            />
          )}

          {page === "mcp-tools" && (
            <McpToolsSettings
              enabled={open}
              backendOnline={backendOnline}
            />
          )}
        </SettingsShell>
      </div>
    </div>
  );
}