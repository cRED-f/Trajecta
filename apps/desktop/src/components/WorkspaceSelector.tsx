import { isTauri } from "@tauri-apps/api/core";

import { open } from "@tauri-apps/plugin-dialog";

import { ChevronDown, FolderOpen } from "lucide-react";

import { useState } from "react";

interface Props {
  /** Host folder this conversation is pinned to, or null for the default. */
  workspacePath: string | null;

  /** A run or a pending approval owns the conversation. */
  disabled?: boolean;

  /**
   * Adopt the picked folder. The selector keeps showing the previous path
   * until this resolves, so a rejected swap never lies about what is active.
   */
  onSelect(path: string): Promise<void> | void;
}

function folderName(path: string | null): string {
  if (!path) {
    return "Open folder";
  }

  const parts = path.split(/[\\/]/).filter(Boolean);

  return parts[parts.length - 1] ?? path;
}

export function WorkspaceSelector({
  workspacePath,
  disabled = false,
  onSelect,
}: Props) {
  const [loading, setLoading] = useState(false);

  const [error, setError] = useState<string | null>(null);

  async function selectFolder() {
    if (disabled || loading) {
      return;
    }

    setError(null);
    setLoading(true);

    try {
      if (!isTauri()) {
        throw new Error(
          "Native folder selection requires the Trajecta desktop app.",
        );
      }

      const selected = await open({
        directory: true,
        multiple: false,
        title: "Select Trajecta Workspace",
        ...(workspacePath ? { defaultPath: workspacePath } : {}),
      });

      if (typeof selected !== "string") {
        return;
      }

      if (selected === workspacePath) {
        return;
      }

      await onSelect(selected);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Could not open workspace.",
      );
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="workspace-selector-container">
      <button
        type="button"
        className={`workspace-selector ${
          workspacePath ? "workspace-selector--set" : ""
        }`}
        onClick={() => void selectFolder()}
        disabled={disabled || loading}
        title={workspacePath ?? "Select working folder"}
        aria-label={
          workspacePath
            ? `Workspace: ${workspacePath}. Change folder`
            : "Select working folder"
        }
      >
        <FolderOpen size={15} />

        <span className="workspace-selector__name">
          {loading ? "Opening…" : folderName(workspacePath)}
        </span>

        <ChevronDown size={13} />
      </button>

      {error && (
        <p className="workspace-selector__error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
