import {
  CalendarDays,
  ChartNoAxesCombined,
  LibraryBig,
  Search,
  Settings2,
  MessageSquarePlus,
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

import type {
  TopLevelPage,
} from "../stores/chat-store";

interface Props {
  conversations:
    | Conversation[]
    | undefined;

  activeId:
    | string
    | null;

  activePage: TopLevelPage | null;

  onNew(): void;

  onSelect(id: string): void;

  onDelete(id: string): void;

  onNavigate(page: TopLevelPage): void;

  onOpenSettings(): void;
}

export function Sidebar({
  conversations,
  activeId,
  activePage,
  onNew,
  onSelect,
  onDelete,
  onNavigate,
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
          <MessageSquarePlus size={18} strokeWidth={1.8} />

          <span>New chat</span>
        </button>

        <div className="sidebar-search">
          <Search size={17} strokeWidth={1.8} />

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
            <div
              key={conversation.id}
              role="button"
              tabIndex={0}
              aria-label={`Open conversation: ${conversation.title ?? "New conversation"}`}
              onKeyDown={(event) => {
                if (event.target !== event.currentTarget) return;
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  onSelect(conversation.id);
                }
              }}
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
                onClick={(event) => {
                  event.stopPropagation();
                  onDelete(conversation.id);
                }}
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
            </div>
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
          className={`sidebar-item sidebar-item--action ${
            activePage === "knowledge" || activePage === "memory" || activePage === "skills" ? "sidebar-item--active" : ""
          }`}
          type="button"
          aria-current={["knowledge", "memory", "skills"].includes(activePage ?? "") ? "page" : undefined}
          onClick={() => onNavigate("knowledge")}
        >
          <span className="sidebar-nav-icon"><LibraryBig size={18} strokeWidth={1.8} /></span>
          <span>Knowledge</span>
        </button>

        <button
          className={`sidebar-item sidebar-item--action ${
            activePage === "scheduled-tasks" ? "sidebar-item--active" : ""
          }`}
          type="button"
          onClick={() => onNavigate("scheduled-tasks")}
        >
          <span className="sidebar-nav-icon"><CalendarDays size={18} strokeWidth={1.8} /></span>
          <span>Scheduled Tasks</span>
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
          <span className="sidebar-nav-icon"><ChartNoAxesCombined size={18} strokeWidth={1.8} /></span>
          <span>Bifrost Dashboard</span>
        </button>

        <button
          className="sidebar-item sidebar-item--action"
          type="button"
          onClick={onOpenSettings}
        >
          <span className="sidebar-nav-icon"><Settings2 size={18} strokeWidth={1.8} /></span>
          <span>
            Settings
          </span>
        </button>
      </footer>
    </aside>
  );
}