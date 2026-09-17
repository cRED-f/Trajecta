import {
  BookMarked,
  ExternalLink,
  Search,
  Settings,
  SquarePen,
  Trash2,
} from "lucide-react";

import { openUrl } from "@tauri-apps/plugin-opener";

import {
  useMemo,
  useState,
} from "react";

import {
  relativeTime,
} from "../lib/format";

import type {
  Conversation,
} from "../types/chat";

interface Props {
  conversations:
    | Conversation[]
    | undefined;

  activeId:
    | string
    | null;

  onNew(): void;

  onSelect(id: string): void;

  onDelete(id: string): void;

  onOpenMemory(): void;

  onOpenSettings(): void;
}

export function Sidebar({
  conversations,
  activeId,
  onNew,
  onSelect,
  onDelete,
  onOpenMemory,
  onOpenSettings,
}: Props) {
  const [search, setSearch] =
    useState("");

  const filtered = useMemo(
    () => {
      const value =
        search
          .trim()
          .toLowerCase();

      if (!value) {
        return (
          conversations ?? []
        );
      }

      return (
        conversations ?? []
      ).filter(
        (conversation) =>
          (
            conversation.title ??
            "New conversation"
          )
            .toLowerCase()
            .includes(value),
      );
    },

    [
      conversations,
      search,
    ],
  );

  return (
    <aside className="sidebar">
      <div className="sidebar-header">
        <button
          className="sidebar-item sidebar-item--action"
          type="button"
          onClick={onNew}
        >
          <SquarePen
            size={16}
          />

          <span>New chat</span>
        </button>

        <div className="sidebar-search">
          <Search
            size={15}
          />

          <input
            type="search"
            placeholder="Search chats"
            value={search}
            onChange={(event) =>
              setSearch(
                event.target.value,
              )
            }
          />
        </div>
      </div>

      <div className="sidebar-section-label">
        Recent
      </div>

      <nav className="conversation-list">
        {filtered.map(
          (conversation) => (
            <button
              key={
                conversation.id
              }
              type="button"
              className={`conversation-card ${
                conversation.id ===
                activeId
                  ? "conversation-card--active"
                  : ""
              }`}
              onClick={() =>
                onSelect(
                  conversation.id,
                )
              }
            >
              <span className="conversation-card__title">
                {conversation.title ??
                  "New conversation"}
              </span>

              <button
                className="conversation-card__delete"
                type="button"
                onClick={() =>
                  onDelete(
                    conversation.id,
                  )
                }
                aria-label="Delete conversation"
                title="Delete"
              >
                <Trash2 size={14} />
              </button>

              <span className="conversation-card__preview">
                {relativeTime(
                  conversation.updated_at,
                )}
              </span>
            </button>
          ),
        )}

        {filtered.length ===
          0 && (
          <div className="sidebar-empty">
            No conversations
          </div>
        )}
      </nav>

      <footer className="sidebar-footer">
        <div className="hairline" />

        <button
          className="sidebar-item sidebar-item--action"
          type="button"
          onClick={onOpenMemory}
        >
          <BookMarked size={16} />
          <span>Saved memory</span>
        </button>

        <button
          className="sidebar-item sidebar-item--action"
          type="button"
          onClick={() =>
            void openUrl(
              "http://127.0.0.1:8080",
            )
          }
        >
          <ExternalLink size={16} />
          <span>Bifrost Dashboard</span>
        </button>

        <button
          className="sidebar-item sidebar-item--action"
          type="button"
          onClick={onOpenSettings}
        >
          <Settings size={16} />
          <span>
            Settings
          </span>
        </button>
      </footer>
    </aside>
  );
}