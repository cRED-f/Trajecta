import {
  GitBranch,
} from "lucide-react";

import type {
  ChatBranch,
  ModelCatalog,
} from "../types/chat";

import {
  basenameModel,
} from "../lib/format";

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
  onModelChange,
  onBranchChange,
}: Props) {
  return (
    <header className="chat-header">
      <select
        className="model-select"
        value={model}
        disabled={disabled}
        onChange={(event) =>
          onModelChange(
            event.target.value,
          )
        }
        aria-label="Model"
      >
        {models?.models.map(
          (item) => (
            <option
              value={item.id}
              key={item.id}
            >
              {basenameModel(
                item.id,
              )}
            </option>
          ),
        )}

        {!models?.models.some(
          (item) =>
            item.id === model,
        ) && (
          <option value={model}>
            {basenameModel(model)}
          </option>
        )}
      </select>

      {branches &&
        branches.length > 1 && (
          <label className="branch-select">
            <GitBranch size={14} />

            <select
              value={
                activeBranchId ??
                ""
              }
              disabled={
                disabled
              }
              onChange={(event) =>
                onBranchChange(
                  event.target
                    .value,
                )
              }
              aria-label="Conversation branch"
            >
              {branches.map(
                (
                  branch,
                  index,
                ) => (
                  <option
                    key={
                      branch.id
                    }
                    value={
                      branch.id
                    }
                  >
                    {branch.label ??
                      (index ===
                      0
                        ? "Main"
                        : `Branch ${
                            index +
                            1
                          }`)}
                  </option>
                ),
              )}
            </select>
          </label>
        )}
    </header>
  );
}