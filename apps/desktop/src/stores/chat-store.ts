import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";

import type {
  ChatMessage,
  ChatStreamEvent,
  StreamState,
  ToolActivity,
} from "../types/chat";

interface EditState {
  kind: "edit";
  message: ChatMessage;
}

type ComposeMode =
  | {
      kind: "normal";
    }
  | EditState;

interface ChatStore {
  activeConversationId: string | null;

  draft: string;
  pendingFiles: File[];

  composeMode: ComposeMode;

  newConversationModel: string | null;

  sidebarOpen: boolean;

  theme: "light" | "dark" | "system";

  showsSettings: boolean;

  streams: Record<string, StreamState>;

  setActiveConversation(id: string | null): void;

  toggleSidebar(): void;

  toggleTheme(): void;

  setTheme(theme: "light" | "dark" | "system"): void;

  setShowsSettings(value: boolean): void;

  setDraft(value: string): void;

  addFiles(files: File[]): void;

  removeFile(index: number): void;

  clearFiles(): void;

  resetComposer(): void;

  startEdit(message: ChatMessage): void;

  cancelEdit(): void;

  setNewConversationModel(model: string | null): void;

  beginStream(conversationId: string): void;

  consumeStreamEvent(conversationId: string, event: ChatStreamEvent): void;

  failStream(conversationId: string, error: string): void;

  clearStream(conversationId: string): void;
}

const EMPTY_STREAM: StreamState = {
  running: false,
  runId: null,
  text: "",
  tools: [],
  steps: [],
  error: null,
};

function toolArgumentString(value: unknown): string {
  if (value === undefined) {
    return "";
  }

  if (typeof value === "string") {
    return value;
  }

  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

let toolSeq = 0;

function updateTool(
  tools: ToolActivity[],
  event: ChatStreamEvent,
): ToolActivity[] {
  const data = event.data;

  if (event.type === "tool.call.delta") {
    const id = typeof data.id === "string" ? data.id : undefined;

    const name = typeof data.name === "string" ? data.name : undefined;

    // Only the first chunk carries id/name; later chunks stream args.
    // Key an id-less chunk to the last running tool so all chunks of one
    // call accumulate into a single activity row.
    let index = id != null ? tools.findIndex((tool) => tool.id === id) : -1;

    if (index === -1 && name != null) {
      index = tools.findIndex(
        (tool) => tool.id == null && tool.name === name && !tool.result,
      );
    }

    if (index === -1) {
      for (let i = tools.length - 1; i >= 0; i -= 1) {
        if (tools[i]!.status === "running" && !tools[i]!.result) {
          index = i;

          break;
        }
      }
    }

    const args = toolArgumentString(data.args);

    if (index === -1) {
      toolSeq += 1;

      return [
        ...tools,

        {
          key: id ?? `tool-${toolSeq}`,
          id,
          name,
          args,
          status: "running",
          source:
            typeof data.source === "string"
              ? data.source
              : undefined,
        },
      ];
    }

    const next = [...tools];

    const current = next[index]!;

    next[index] = {
      ...current,

      name: name ?? current.name,

      id: id ?? current.id,

      args: current.args + args,
    };

    return next;
  }

  if (event.type === "tool.result") {
    const id =
      typeof data.tool_call_id === "string" ? data.tool_call_id : undefined;

    let index = id ? tools.findIndex((tool) => tool.id === id) : -1;

    if (index === -1) {
      index = tools.length - 1;
    }

    if (index < 0) {
      return tools;
    }

    const next = [...tools];

    const current = next[index]!;

    next[index] = {
      ...current,

      result:
        typeof data.content === "string"
          ? data.content
          : String(
              data.content ?? "",
            ),

      status:
        typeof data.status === "string"
          ? data.status
          : "success",
    };

    return next;
  }

  return tools;
}

export const useChatStore = create<ChatStore>()(
  persist(
    (set) => ({
      activeConversationId: null,

      draft: "",
      pendingFiles: [],

      composeMode: {
        kind: "normal",
      },

      newConversationModel: null,

      sidebarOpen: true,

      theme: "system",

      showsSettings: false,

      streams: {},

      setActiveConversation(id) {
        set({
          activeConversationId: id,
          draft: "",
          pendingFiles: [],
          composeMode: {
            kind: "normal",
          },
        });
      },

      setDraft(value) {
        set({
          draft: value,
        });
      },

      addFiles(files) {
        set((state) => ({
          pendingFiles: [...state.pendingFiles, ...files],
        }));
      },

      removeFile(index) {
        set((state) => ({
          pendingFiles: state.pendingFiles.filter(
            (_, current) => current !== index,
          ),
        }));
      },

      clearFiles() {
        set({
          pendingFiles: [],
        });
      },

      resetComposer() {
        set({
          draft: "",
          pendingFiles: [],
          composeMode: {
            kind: "normal",
          },
        });
      },

      startEdit(message) {
        set({
          draft: message.content,

          pendingFiles: [],

          composeMode: {
            kind: "edit",
            message,
          },
        });
      },

      cancelEdit() {
        set({
          draft: "",
          pendingFiles: [],
          composeMode: {
            kind: "normal",
          },
        });
      },

      setNewConversationModel(model) {
        set({
          newConversationModel: model,
        });
      },

      toggleSidebar() {
        set((state) => ({
          sidebarOpen:
            !state.sidebarOpen,
        }));
      },

      toggleTheme() {
        set((state) => ({
          theme:
            state.theme === "dark"
              ? "system"
              : state.theme === "system"
                ? "light"
                : "dark",
        }));
      },

      setTheme(theme) {
        set({ theme });
      },

      setShowsSettings(value) {
        set({ showsSettings: value });
      },

      beginStream(conversationId) {
        set((state) => ({
          streams: {
            ...state.streams,

            [conversationId]: {
              ...EMPTY_STREAM,
              running: true,
            },
          },
        }));
      },

      consumeStreamEvent(conversationId, event) {
        set((state) => {
          const current = state.streams[conversationId] ?? EMPTY_STREAM;

          let next: StreamState = {
            ...current,

            runId: event.run_id ?? current.runId,
          };

          if (event.type === "message.delta") {
            const text =
              typeof event.data.text === "string" ? event.data.text : "";

            next = {
              ...next,
              text: next.text + text,
            };
          }

          if (
            event.type === "tool.call.delta" ||
            event.type === "tool.result"
          ) {
            next = {
              ...next,

              tools: updateTool(next.tools, event),
            };
          }

          if (event.type === "agent.step") {
            const node =
              typeof event.data.node === "string" ? event.data.node : null;

            if (node) {
              next = {
                ...next,

                steps: [...next.steps, node].slice(-8),
              };
            }
          }

          if (event.type === "run.error") {
            next = {
              ...next,
              running: false,

              error:
                typeof event.data.error === "string"
                  ? event.data.error
                  : "Agent run failed.",
            };
          }

          if (
            event.type === "run.cancelled" ||
            event.type === "message.completed"
          ) {
            next = {
              ...next,
              running: false,
            };
          }

          return {
            streams: {
              ...state.streams,
              [conversationId]: next,
            },
          };
        });
      },

      failStream(conversationId, error) {
        set((state) => ({
          streams: {
            ...state.streams,

            [conversationId]: {
              ...(state.streams[conversationId] ?? EMPTY_STREAM),

              running: false,
              error,
            },
          },
        }));
      },

      clearStream(conversationId) {
        set((state) => {
          const next = {
            ...state.streams,
          };

          delete next[conversationId];

          return {
            streams: next,
          };
        });
      },
    }),

    {
      name: "trajecta-ui",

      storage: createJSONStorage(() => localStorage),

      partialize: (state) => ({
        activeConversationId:
          state.activeConversationId,

        newConversationModel:
          state.newConversationModel,

        sidebarOpen:
          state.sidebarOpen,

        theme:
          state.theme,
      }),

      // showsSettings is session-only (not persisted)
    },
  ),
);
