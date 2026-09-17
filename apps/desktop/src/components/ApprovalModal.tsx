import {
  Check,
  LoaderCircle,
  MessageSquare,
  Pencil,
  ShieldAlert,
  X,
} from "lucide-react";

import {
  useEffect,
  useMemo,
  useState,
} from "react";

import type {
  ApprovalActionRequest,
  ApprovalDecision,
  ApprovalDecisionType,
  ApprovalInterruptData,
  ApprovalReviewConfig,
  PendingApproval,
} from "../types/chat";

interface ActionEntry {
  action: ApprovalActionRequest;
  review?: ApprovalReviewConfig;
}

interface DecisionDraft {
  type?: ApprovalDecisionType;
  message: string;
  editedAction: string;
}

interface Props {
  approval: PendingApproval | null | undefined;

  submitting: boolean;

  error?: string | null;

  onSubmit(
    decisions: ApprovalDecision[],
  ): Promise<void> | void;
}

function collectActions(
  data: ApprovalInterruptData,
): ActionEntry[] {
  const result: ActionEntry[] = [];

  const requests = data.action_requests ?? [];

  const configs = data.review_configs ?? [];

  for (const action of requests) {
    result.push({
      action,

      review: configs.find(
        (item) =>
          item.action_name === action.name,
      ),
    });
  }

  for (const nested of data.interrupts ?? []) {
    result.push(...collectActions(nested));
  }

  return result;
}

function pretty(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function humanizeToolName(value: string): string {
  return value
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (character) =>
      character.toUpperCase(),
    );
}

function defaultDraft(
  entry: ActionEntry,
): DecisionDraft {
  return {
    message: "",

    editedAction: pretty({
      name: entry.action.name,
      args: entry.action.args ?? {},
    }),
  };
}

function allowedDecisions(
  entry: ActionEntry,
): ApprovalDecisionType[] {
  const configured = entry.review?.allowed_decisions;

  if (configured && configured.length > 0) {
    return configured;
  }

  return ["approve", "edit", "reject"];
}

export function ApprovalModal({
  approval,
  submitting,
  error,
  onSubmit,
}: Props) {
  const entries = useMemo(
    () =>
      approval ? collectActions(approval.interrupt_data) : [],

    [approval],
  );

  const [drafts, setDrafts] = useState<DecisionDraft[]>([]);

  const [localError, setLocalError] = useState<string | null>(null);

  useEffect(() => {
    setDrafts(entries.map(defaultDraft));

    setLocalError(null);
  }, [approval?.id, entries]);

  if (!approval) {
    return null;
  }

  function updateDraft(
    index: number,
    patch: Partial<DecisionDraft>,
  ) {
    setDrafts((current) =>
      current.map((item, itemIndex) =>
        itemIndex === index
          ? { ...item, ...patch }
          : item,
      ),
    );
  }

  async function submit() {
    setLocalError(null);

    if (entries.length === 0) {
      setLocalError(
        "The agent paused, but no reviewable action was included in the interrupt.",
      );

      return;
    }

    if (drafts.some((item) => !item.type)) {
      setLocalError("Choose a decision for every pending action.");

      return;
    }

    const decisions: ApprovalDecision[] = [];

    for (let index = 0; index < entries.length; index += 1) {
      const entry = entries[index]!;
      const draft = drafts[index]!;

      if (draft.type === "edit") {
        let parsed: unknown;

        try {
          parsed = JSON.parse(draft.editedAction);
        } catch {
          setLocalError(`Edited action ${index + 1} is not valid JSON.`);

          return;
        }

        if (
          typeof parsed !== "object" ||
          parsed === null ||
          !("name" in parsed) ||
          !("args" in parsed) ||
          typeof (parsed as { name?: unknown }).name !== "string" ||
          typeof (parsed as { args?: unknown }).args !== "object" ||
          (parsed as { args?: unknown }).args === null
        ) {
          setLocalError(
            `Edited action ${index + 1} must contain a string "name" and object "args".`,
          );

          return;
        }

        decisions.push({
          type: "edit",

          edited_action: parsed as {
            name: string;
            args: Record<string, unknown>;
          },
        });

        continue;
      }

      if (draft.type === "respond") {
        if (!draft.message.trim()) {
          setLocalError(`Action ${index + 1} needs a response before continuing.`);

          return;
        }

        decisions.push({
          type: "respond",
          message: draft.message.trim(),
        });

        continue;
      }

      if (draft.type === "reject") {
        decisions.push({
          type: "reject",

          ...(draft.message.trim()
            ? { message: draft.message.trim() }
            : {}),
        });

        continue;
      }

      decisions.push({ type: "approve" });

      void entry;
    }

    await onSubmit(decisions);
  }

  return (
    <div className="approval-overlay" role="presentation">
      <section
        className="approval-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="approval-title"
      >
        <header className="approval-modal__header">
          <div className="approval-modal__icon">
            <ShieldAlert size={20} />
          </div>

          <div>
            <h2 id="approval-title">Permission required</h2>

            <p>
              Trajecta paused before performing{" "}
              {entries.length === 1
                ? "a sensitive action"
                : `${entries.length} sensitive actions`}
              .
            </p>
          </div>
        </header>

        <div className="approval-modal__body">
          {entries.length === 0 ? (
            <pre className="approval-raw">{pretty(approval.interrupt_data)}</pre>
          ) : (
            entries.map((entry, index) => {
              const draft = drafts[index] ?? defaultDraft(entry);

              const allowed = allowedDecisions(entry);

              return (
                <article
                  className="approval-action"
                  key={`${entry.action.name}-${index}`}
                >
                  <div className="approval-action__heading">
                    <div>
                      <span className="approval-action__eyebrow">
                        Action {index + 1} of {entries.length}
                      </span>

                      <h3>{humanizeToolName(entry.action.name)}</h3>
                    </div>

                    <code>{entry.action.name}</code>
                  </div>

                  {entry.action.description && (
                    <p className="approval-action__description">
                      {entry.action.description}
                    </p>
                  )}

                  <div className="approval-action__arguments">
                    <span>Arguments</span>

                    <pre>{pretty(entry.action.args ?? {})}</pre>
                  </div>

                  <div className="approval-action__choices">
                    {allowed.includes("approve") && (
                      <button
                        className={`approval-choice ${
                          draft.type === "approve"
                            ? "approval-choice--selected approval-choice--approve"
                            : ""
                        }`}
                        type="button"
                        disabled={submitting}
                        onClick={() =>
                          updateDraft(index, { type: "approve" })
                        }
                      >
                        <Check size={15} />
                        Approve
                      </button>
                    )}

                    {allowed.includes("reject") && (
                      <button
                        className={`approval-choice ${
                          draft.type === "reject"
                            ? "approval-choice--selected approval-choice--reject"
                            : ""
                        }`}
                        type="button"
                        disabled={submitting}
                        onClick={() =>
                          updateDraft(index, { type: "reject" })
                        }
                      >
                        <X size={15} />
                        Reject
                      </button>
                    )}

                    {allowed.includes("edit") && (
                      <button
                        className={`approval-choice ${
                          draft.type === "edit"
                            ? "approval-choice--selected"
                            : ""
                        }`}
                        type="button"
                        disabled={submitting}
                        onClick={() =>
                          updateDraft(index, { type: "edit" })
                        }
                      >
                        <Pencil size={14} />
                        Edit
                      </button>
                    )}

                    {allowed.includes("respond") && (
                      <button
                        className={`approval-choice ${
                          draft.type === "respond"
                            ? "approval-choice--selected"
                            : ""
                        }`}
                        type="button"
                        disabled={submitting}
                        onClick={() =>
                          updateDraft(index, { type: "respond" })
                        }
                      >
                        <MessageSquare size={14} />
                        Respond
                      </button>
                    )}
                  </div>

                  {draft.type === "edit" && (
                    <label className="approval-field">
                      <span>Edited action</span>

                      <textarea
                        value={draft.editedAction}
                        disabled={submitting}
                        spellCheck={false}
                        onChange={(event) =>
                          updateDraft(index, {
                            editedAction: event.target.value,
                          })
                        }
                      />
                    </label>
                  )}

                  {(draft.type === "reject" || draft.type === "respond") && (
                    <label className="approval-field">
                      <span>
                        {draft.type === "respond"
                          ? "Your response"
                          : "Reason (optional)"}
                      </span>

                      <textarea
                        value={draft.message}
                        disabled={submitting}
                        placeholder={
                          draft.type === "respond"
                            ? "Answer the agent..."
                            : "Tell Trajecta why this action should not run..."
                        }
                        onChange={(event) =>
                          updateDraft(index, {
                            message: event.target.value,
                          })
                        }
                      />
                    </label>
                  )}
                </article>
              );
            })
          )}

          {(localError || error) && (
            <div className="approval-error">{localError ?? error}</div>
          )}
        </div>

        <footer className="approval-modal__footer">
          <span>Nothing runs until you continue.</span>

          <button
            className="approval-continue"
            type="button"
            disabled={
              submitting ||
              entries.length === 0 ||
              drafts.some((item) => !item.type)
            }
            onClick={() => void submit()}
          >
            {submitting ? (
              <>
                <LoaderCircle className="approval-spinner" size={15} />
                Resuming
              </>
            ) : (
              "Continue"
            )}
          </button>
        </footer>
      </section>
    </div>
  );
}