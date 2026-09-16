import {
  Search,
  SquarePen,
} from "lucide-react";

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

  backendOnline: boolean;

  onNew(): void;

  onSelect(id: string): void;
}

export function Sidebar({
  conversations,
  activeId,
  backendOnline,
  onNew,
  onSelect,
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

        <div className="runtime-status">
          <span
            className={`runtime-status__indicator ${
              backendOnline
                ? "runtime-status__indicator--online"
                : ""
            }`}
          />

          <div>
            <div className="runtime-status__title">
              Local runtime
            </div>

            <div className="runtime-status__copy">
              {backendOnline
                ? "Connected"
                : "Backend unavailable"}
            </div>
          </div>
        </div>
      </footer>
    </aside>
  );
}