import {
  AlertCircle,
  PlugZap,
  RefreshCw,
  Search,
  Wrench,
} from "lucide-react";

import { useMemo, useState } from "react";

import { useMcpActions, useMcpCatalog } from "../../hooks/use-tools";

import type { McpServerStatus } from "../../types/tools";

import { SettingsToggle } from "./SettingsToggle";

function ServerStatus({
  status,
}: {
  status: McpServerStatus;
}) {
  return (
    <span className={`mcp-status mcp-status--${status}`}>
      <span className="mcp-status__dot" />

      {status === "connected"
        ? "Connected"
        : status === "error"
          ? "Unavailable"
          : "Unknown"}
    </span>
  );
}

interface Props {
  enabled: boolean;
  backendOnline: boolean;
}

export function McpToolsSettings({
  enabled,
  backendOnline,
}: Props) {
  const [search, setSearch] = useState("");

  const mcpQuery = useMcpCatalog(enabled && backendOnline);
  const mcpActions = useMcpActions();

  const filteredServers = useMemo(() => {
    const servers = mcpQuery.data?.servers ?? [];

    const query = search.trim().toLowerCase();

    if (!query) {
      return servers;
    }

    return servers
      .map((server) => {
        const serverMatches = server.name
          .toLowerCase()
          .includes(query);

        if (serverMatches) {
          return server;
        }

        const tools = server.tools.filter(
          (tool) =>
            tool.name.toLowerCase().includes(query) ||
            tool.description.toLowerCase().includes(query),
        );

        if (tools.length === 0) {
          return null;
        }

        return { ...server, tools };
      })
      .filter(
        (
          server,
        ): server is NonNullable<typeof server> =>
          server !== null,
      );
  }, [mcpQuery.data, search]);

  return (
    <section className="mcp-settings">
      <div className="settings-page-header">
        <div>
          <h3>MCP Tools</h3>
          <p>Choose which connected MCP tools Trajecta can use.</p>
        </div>

        <button
          type="button"
          className="settings-secondary-button"
          disabled={!backendOnline || mcpActions.refreshing}
          onClick={() => void mcpActions.refresh()}
        >
          <RefreshCw
            size={14}
            className={mcpActions.refreshing ? "settings-spin" : ""}
          />
          Refresh
        </button>
      </div>

      {!backendOnline ? (
        <div className="settings-empty-state">
          <AlertCircle size={22} />

          <strong>Backend disconnected</strong>

          <span>
            Connect to the Trajecta backend to manage MCP tools.
          </span>
        </div>
      ) : (
        <>
          <div className="mcp-toolbar">
            <div className="mcp-search">
              <Search size={15} />

              <input
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="Search MCP tools..."
              />
            </div>

            {mcpQuery.data && (
              <span className="mcp-summary">
                {mcpQuery.data.enabled_count} of{" "}
                {mcpQuery.data.tool_count} enabled
              </span>
            )}
          </div>

          {mcpQuery.isLoading && (
            <div className="settings-loading">
              <RefreshCw className="settings-spin" size={17} />
              Discovering MCP tools…
            </div>
          )}

          {mcpQuery.isError && (
            <div className="settings-error-card">
              <AlertCircle size={17} />

              <span>
                {mcpQuery.error instanceof Error
                  ? mcpQuery.error.message
                  : "Failed to load MCP tools."}
              </span>
            </div>
          )}

          {!mcpQuery.isLoading &&
            !mcpQuery.isError &&
            filteredServers.length === 0 && (
              <div className="settings-empty-state">
                <Wrench size={22} />

                <strong>No MCP tools found</strong>

                <span>
                  {search
                    ? "No tools match your search."
                    : "Configure an MCP server to make its tools available here."}
                </span>
              </div>
            )}

          <div className="mcp-server-list">
            {filteredServers.map((server) => (
              <div className="mcp-server-card" key={server.name}>
                <div className="mcp-server-header">
                  <div className="mcp-server-icon">
                    <PlugZap size={17} />
                  </div>

                  <div className="mcp-server-info">
                    <div className="mcp-server-title-row">
                      <strong>{server.name}</strong>

                      <ServerStatus status={server.status} />
                    </div>

                    <span>
                      {server.enabled_count} of {server.tool_count} tools
                      enabled
                    </span>
                  </div>

                  <SettingsToggle
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

                {server.tools.length > 0 && (
                  <div className="mcp-tool-list">
                    {server.tools.map((tool) => (
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

                        <SettingsToggle
                          checked={tool.enabled}
                          disabled={!server.enabled || mcpActions.updatingTool}
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
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>

          <div className="mcp-settings-note">
            Disabled tools are removed from Trajecta's tool set on the next
            agent run. Active runs are not changed.
          </div>
        </>
      )}
    </section>
  );
}