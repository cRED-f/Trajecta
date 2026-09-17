import {
  AlertCircle,
  CheckCircle2,
  Moon,
  Palette,
  PlugZap,
  RefreshCw,
  Search,
  Settings,
  Sun,
  Wrench,
  X,
} from "lucide-react";

import {
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import {
  useMcpActions,
  useMcpCatalog,
} from "../hooks/use-tools";

type SettingsPage =
  | "general"
  | "mcp";

interface Props {
  open: boolean;

  theme:
    | "light"
    | "dark"
    | "system";

  backendOnline:
    boolean;

  onClose(): void;

  onSetTheme(
    theme:
      | "light"
      | "dark"
      | "system",
  ): void;
}

interface ToggleProps {
  checked: boolean;
  disabled?: boolean;
  label: string;
  onChange(
    checked: boolean,
  ): void;
}

function Toggle({
  checked,
  disabled = false,
  label,
  onChange,
}: ToggleProps) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      className={`settings-toggle ${
        checked
          ? "settings-toggle--on"
          : ""
      }`}
      onClick={() =>
        onChange(!checked)
      }
    >
      <span className="settings-toggle__thumb" />
    </button>
  );
}

export function SettingsModal({
  open,
  theme,
  backendOnline,
  onClose,
  onSetTheme,
}: Props) {
  const overlayRef =
    useRef<HTMLDivElement>(null);

  const [page, setPage] =
    useState<SettingsPage>(
      "general",
    );

  const [search, setSearch] =
    useState("");

  const mcpQuery = useMcpCatalog(
    open &&
      page === "mcp" &&
      backendOnline,
  );

  const mcpActions =
    useMcpActions();

  useEffect(() => {
    if (!open) return;

    function onKeydown(
      event: KeyboardEvent,
    ) {
      if (
        event.key === "Escape"
      ) {
        event.preventDefault();
        onClose();
      }
    }

    window.addEventListener(
      "keydown",
      onKeydown,
    );

    return () =>
      window.removeEventListener(
        "keydown",
        onKeydown,
      );
  }, [open, onClose]);

  useEffect(() => {
    if (!open) {
      setSearch("");
    }
  }, [open]);

  const filteredServers =
    useMemo(() => {
      const servers =
        mcpQuery.data
          ?.servers ?? [];

      const query = search
        .trim()
        .toLowerCase();

      if (!query) {
        return servers;
      }

      return servers
        .map((server) => {
          const serverMatches =
            server.name
              .toLowerCase()
              .includes(query);

          if (serverMatches) {
            return server;
          }

          const tools =
            server.tools.filter(
              (tool) =>
                tool.name
                  .toLowerCase()
                  .includes(query) ||
                tool.description
                  .toLowerCase()
                  .includes(query),
            );

          if (
            tools.length === 0
          ) {
            return null;
          }

          return {
            ...server,
            tools,
          };
        })
        .filter(
          (
            server,
          ): server is NonNullable<
            typeof server
          > =>
            server !== null,
        );
    }, [
      mcpQuery.data,
      search,
    ]);

  if (!open) {
    return null;
  }

  return (
    <div
      className="settings-overlay"
      ref={overlayRef}
      onClick={(event) => {
        if (
          event.target ===
          overlayRef.current
        ) {
          onClose();
        }
      }}
    >
      <div className="settings-modal settings-modal--large">
        <div className="settings-modal__header">
          <div>
            <h2>Settings</h2>

            <p>
              Configure Trajecta
              and connected tools.
            </p>
          </div>

          <button
            type="button"
            className="settings-close"
            onClick={onClose}
            aria-label="Close settings"
          >
            <X size={17} />
          </button>
        </div>

        <div className="settings-layout">
          <nav className="settings-nav">
            <button
              type="button"
              className={`settings-nav__item ${
                page === "general"
                  ? "settings-nav__item--active"
                  : ""
              }`}
              onClick={() =>
                setPage("general")
              }
            >
              <Settings
                size={16}
              />
              General
            </button>

            <button
              type="button"
              className={`settings-nav__item ${
                page === "mcp"
                  ? "settings-nav__item--active"
                  : ""
              }`}
              onClick={() =>
                setPage("mcp")
              }
            >
              <PlugZap
                size={16}
              />
              MCP Tools
              {mcpQuery.data && (
                <span className="settings-nav__count">
                  {mcpQuery.data.enabled_count}
                </span>
              )}
            </button>
          </nav>

          <main className="settings-content">
            {page ===
              "general" && (
              <GeneralSettings
                theme={theme}
                backendOnline={backendOnline}
                onSetTheme={onSetTheme}
              />
            )}

            {page === "mcp" && (
              <section className="mcp-settings">
                <div className="settings-page-header">
                  <div>
                    <h3>MCP Tools</h3>

                    <p>
                      Choose which
                      connected MCP
                      tools Trajecta
                      can use.
                    </p>
                  </div>

                  <button
                    type="button"
                    className="settings-secondary-button"
                    disabled={
                      !backendOnline ||
                      mcpActions.refreshing
                    }
                    onClick={() =>
                      void mcpActions.refresh()
                    }
                  >
                    <RefreshCw
                      size={14}
                      className={
                        mcpActions.refreshing
                          ? "settings-spin"
                          : ""
                      }
                    />
                    Refresh
                  </button>
                </div>

                {!backendOnline ? (
                  <div className="settings-empty-state">
                    <AlertCircle
                      size={22}
                    />

                    <strong>
                      Backend
                      disconnected
                    </strong>

                    <span>
                      Connect to the
                      Trajecta backend
                      to manage MCP
                      tools.
                    </span>
                  </div>
                ) : (
                  <>
                    <div className="mcp-toolbar">
                      <div className="mcp-search">
                        <Search
                          size={15}
                        />

                        <input
                          value={search}
                          onChange={(event) =>
                            setSearch(
                              event.target
                                .value,
                            )
                          }
                          placeholder="Search MCP tools..."
                        />
                      </div>

                      {mcpQuery.data && (
                        <span className="mcp-summary">
                          {mcpQuery.data.enabled_count}{" "}
                          of{" "}
                          {mcpQuery.data.tool_count}{" "}
                          enabled
                        </span>
                      )}
                    </div>

                    {mcpQuery.isLoading && (
                      <div className="settings-loading">
                        <RefreshCw
                          className="settings-spin"
                          size={17}
                        />
                        Discovering
                        MCP tools…
                      </div>
                    )}

                    {mcpQuery.isError && (
                      <div className="settings-error-card">
                        <AlertCircle
                          size={17}
                        />

                        <span>
                          {mcpQuery.error instanceof Error
                            ? mcpQuery.error.message
                            : "Failed to load MCP tools."}
                        </span>
                      </div>
                    )}

                    {!mcpQuery.isLoading &&
                      !mcpQuery.isError &&
                      filteredServers.length ===
                        0 && (
                        <div className="settings-empty-state">
                          <Wrench
                            size={22}
                          />

                          <strong>
                            No MCP tools
                            found
                          </strong>

                          <span>
                            {search
                              ? "No tools match your search."
                              : "Configure an MCP server to make its tools available here."}
                          </span>
                        </div>
                      )}

                    <div className="mcp-server-list">
                      {filteredServers.map(
                        (server) => (
                          <div
                            className="mcp-server-card"
                            key={server.name}
                          >
                            <div className="mcp-server-header">
                              <div className="mcp-server-icon">
                                <PlugZap
                                  size={17}
                                />
                              </div>

                              <div className="mcp-server-info">
                                <div className="mcp-server-title-row">
                                  <strong>
                                    {server.name}
                                  </strong>

                                  <ServerStatus
                                    status={server.status}
                                  />
                                </div>

                                <span>
                                  {server.enabled_count}{" "}
                                  of{" "}
                                  {server.tool_count}{" "}
                                  tools enabled
                                </span>
                              </div>

                              <Toggle
                                checked={server.enabled}
                                disabled={mcpActions.updatingServer}
                                label={`Enable ${server.name}`}
                                onChange={(enabled) =>
                                  void mcpActions.setServer({
                                    server: server.name,
                                    enabled,
                                  })
                                }
                              />
                            </div>

                            {server.error && (
                              <div className="mcp-server-error">
                                {server.error}
                              </div>
                            )}

                            {server.tools.length >
                              0 && (
                              <div className="mcp-tool-list">
                                {server.tools.map(
                                  (tool) => (
                                    <div
                                      className="mcp-tool-row"
                                      key={`${server.name}:${tool.name}`}
                                    >
                                      <div className="mcp-tool-info">
                                        <div className="mcp-tool-name">
                                          {tool.name}
                                        </div>

                                        {tool.description && (
                                          <div className="mcp-tool-description">
                                            {tool.description}
                                          </div>
                                        )}
                                      </div>

                                      <Toggle
                                        checked={tool.enabled}
                                        disabled={
                                          !server.enabled ||
                                          mcpActions.updatingTool
                                        }
                                        label={`Enable ${tool.name}`}
                                        onChange={(enabled) =>
                                          void mcpActions.setTool({
                                            server: server.name,
                                            tool: tool.name,
                                            enabled,
                                          })
                                        }
                                      />
                                    </div>
                                  ),
                                )}
                              </div>
                            )}
                          </div>
                        ),
                      )}
                    </div>

                    <div className="mcp-settings-note">
                      Disabled tools are
                      removed from
                      Trajecta's tool
                      set on the next
                      agent run. Active
                      runs are not
                      changed.
                    </div>
                  </>
                )}
              </section>
            )}
          </main>
        </div>
      </div>
    </div>
  );
}

function GeneralSettings({
  theme,
  backendOnline,
  onSetTheme,
}: {
  theme:
    | "light"
    | "dark"
    | "system";
  backendOnline: boolean;
  onSetTheme(
    theme:
      | "light"
      | "dark"
      | "system",
  ): void;
}) {
  return (
    <section>
      <div className="settings-page-header">
        <div>
          <h3>General</h3>

          <p>
            Appearance and
            local service
            configuration.
          </p>
        </div>
      </div>

      <div className="settings-group-card">
        <div className="settings-row">
          <div className="settings-row__icon">
            <Palette
              size={16}
            />
          </div>

          <div className="settings-row__body">
            <strong>Theme</strong>

            <span>
              Choose your
              interface
              appearance.
            </span>
          </div>

          <div className="theme-switcher">
            <button
              type="button"
              className={`theme-switcher__option ${
                theme === "light"
                  ? "theme-switcher__option--active"
                  : ""
              }`}
              onClick={() =>
                onSetTheme("light")
              }
            >
              <Sun size={15} />
              Light
            </button>

            <button
              type="button"
              className={`theme-switcher__option ${
                theme === "dark"
                  ? "theme-switcher__option--active"
                  : ""
              }`}
              onClick={() =>
                onSetTheme("dark")
              }
            >
              <Moon size={15} />
              Dark
            </button>

            <button
              type="button"
              className={`theme-switcher__option ${
                theme === "system"
                  ? "theme-switcher__option--active"
                  : ""
              }`}
              onClick={() =>
                onSetTheme("system")
              }
            >
              <Settings
                size={15}
              />
              System
            </button>
          </div>
        </div>

        <div className="settings-row">
          <div className="settings-row__icon">
            {backendOnline ? (
              <CheckCircle2
                size={16}
              />
            ) : (
              <AlertCircle
                size={16}
              />
            )}
          </div>

          <div className="settings-row__body">
            <strong>Backend</strong>

            <span>
              Trajecta local
              agent service.
            </span>
          </div>

          <span
            className={`settings-status-pill ${
              backendOnline
                ? "settings-status-pill--online"
                : "settings-status-pill--offline"
            }`}
          >
            {backendOnline
              ? "Connected"
              : "Disconnected"}
          </span>
        </div>
      </div>
    </section>
  );
}

function ServerStatus({
  status,
}: {
  status:
    | "connected"
    | "error"
    | "unknown";
}) {
  return (
    <span
      className={`mcp-status mcp-status--${status}`}
    >
      <span className="mcp-status__dot" />

      {status === "connected"
        ? "Connected"
        : status === "error"
          ? "Unavailable"
          : "Unknown"}
    </span>
  );
}