import {
  Brain,
  PlugZap,
  Server,
  Settings,
  ShieldAlert,
  ShieldCheck,
  X,
} from "lucide-react";

import type { ReactNode } from "react";

import type { SettingsPage } from "./settings-types";

interface NavItemProps {
  page: SettingsPage;
  current: SettingsPage;
  icon: ReactNode;
  label: string;
  count?: number;
  onSelect(page: SettingsPage): void;
}

function SettingsNavItem({
  page,
  current,
  icon,
  label,
  count,
  onSelect,
}: NavItemProps) {
  const active = page === current;

  return (
    <button
      type="button"
      className={`settings-sidebar__item ${
        active ? "settings-sidebar__item--active" : ""
      }`}
      aria-current={active ? "page" : undefined}
      onClick={() => onSelect(page)}
    >
      {icon}
      {label}
      {count !== undefined && count >= 0 && (
        <span className="settings-sidebar__count">{count}</span>
      )}
    </button>
  );
}

interface ShellProps {
  page: SettingsPage;
  onPageChange(page: SettingsPage): void;
  onClose(): void;
  children: ReactNode;
  mcpEnabledCount?: number;
}

export function SettingsShell({
  page,
  onPageChange,
  onClose,
  children,
  mcpEnabledCount,
}: ShellProps) {
  return (
    <div className="settings-layout">
      <aside className="settings-sidebar">
        <div className="settings-sidebar__group-label">General</div>

        <SettingsNavItem
          page="general"
          current={page}
          icon={<Settings size={16} />}
          label="General"
          onSelect={onPageChange}
        />

        <div className="settings-sidebar__group-label">
          AI
        </div>

        <SettingsNavItem
          page="llm-providers"
          current={page}
          icon={<Server size={16} />}
          label="LLM Providers"
          onSelect={onPageChange}
        />

        <SettingsNavItem
          page="memory"
          current={page}
          icon={<Brain size={16} />}
          label="Embedding & Learning"
          onSelect={onPageChange}
        />

        <SettingsNavItem
          page="guardrails"
          current={page}
          icon={<ShieldAlert size={16} />}
          label="Guardrails"
          onSelect={onPageChange}
        />

        <SettingsNavItem
          page="permissions"
          current={page}
          icon={<ShieldCheck size={16} />}
          label="Permissions"
          onSelect={onPageChange}
        />

        <div className="settings-sidebar__group-label">
          Tools & Connections
        </div>

        <SettingsNavItem
          page="mcp-tools"
          current={page}
          icon={<PlugZap size={16} />}
          label="MCP Tools"
          count={mcpEnabledCount}
          onSelect={onPageChange}
        />
      </aside>

      <main className="settings-content">{children}</main>
    </div>
  );
}

export function SettingsHeader({
  title,
  subtitle,
  onClose,
  actions,
}: {
  title: string;
  subtitle: string;
  onClose(): void;
  actions?: ReactNode;
}) {
  return (
    <div className="settings-modal__header">
      <div>
        <h2>{title}</h2>
        <p>{subtitle}</p>
      </div>

      {actions}

      <button
        type="button"
        className="settings-close"
        onClick={onClose}
        aria-label="Close settings"
      >
        <X size={17} />
      </button>
    </div>
  );
}