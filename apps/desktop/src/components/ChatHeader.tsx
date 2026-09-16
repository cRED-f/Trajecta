import {
  GitBranch,
  PanelLeftClose,
  PanelLeftOpen,
} from "lucide-react";

import type {
  ChatBranch,
  ModelCatalog,
} from "../types/chat";

import {
  basenameModel,
} from "../lib/format";

import { Dropdown } from "./Dropdown";

interface Props {
  model: string;

  models:
    | ModelCatalog
    | undefined;

  branches:
    | ChatBranch[]
    | undefined;

  activeBranchId:
    | string
    | null;

  disabled?: boolean;

  sidebarOpen: boolean;

  onToggleSidebar(): void;

  onModelChange(
    model: string,
  ): void;

  onBranchChange(
    branchId: string,
  ): void;
}

export function ChatHeader({
  model,
  models,
  branches,
  activeBranchId,
  disabled,
  sidebarOpen,
  onToggleSidebar,
  onModelChange,
  onBranchChange,
}: Props) {
  const modelOptions =
    models?.models.map(
      (item) => ({
        value: item.id,

        label: basenameModel(
          item.id,
        ),
      }),
    ) ?? [];

  const branchOptions =
    branches?.map(
      (branch, index) => ({
        value: branch.id,

        label:
          branch.label ??
          (index === 0
            ? "Main"
            : `Branch ${
                index + 1
              }`),
      }),
    ) ?? [];

  return (
    <header className="chat-header">
      <button
        className="icon-button chat-header__sidebar-toggle"
        type="button"
        onClick={
          onToggleSidebar
        }
        aria-label={
          sidebarOpen
            ? "Hide sidebar"
            : "Show sidebar"
        }
        title={
          sidebarOpen
            ? "Hide sidebar"
            : "Show sidebar"
        }
      >
        {sidebarOpen ? (
          <PanelLeftClose
            size={17}
          />
        ) : (
          <PanelLeftOpen
            size={17}
          />
        )}
      </button>

      <div className="chat-header__divider" />

      <Dropdown
        value={model}
        options={modelOptions}
        disabled={disabled}
        onChange={(value) =>
          onModelChange(value)
        }
      />

      <div className="chat-header__spacer" />

      {branches &&
        branches.length > 1 && (
          <label className="branch-select">
            <GitBranch
              size={14}
            />

            <select
              value={
                activeBranchId ??
                ""
              }
              disabled={
                disabled
              }
              onChange={(
                event,
              ) =>
                onBranchChange(
                  event.target
                    .value,
                )
              }
            >
              {branchOptions.map(
                (option) => (
                  <option
                    key={
                      option.value
                    }
                    value={
                      option.value
                    }
                  >
                    {option.label}
                  </option>
                ),
              )}
            </select>
          </label>
        )}
    </header>
  );
}
