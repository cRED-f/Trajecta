import { AlertCircle, CheckCircle2, Moon, Palette, Settings, Sun } from "lucide-react";

import type { ThemeValue } from "./settings-types";

interface Props {
  theme: ThemeValue;
  backendOnline: boolean;
  onSetTheme(theme: ThemeValue): void;
}

export function GeneralSettings({
  theme,
  backendOnline,
  onSetTheme,
}: Props) {
  return (
    <section>
      <div className="settings-page-header">
        <div>
          <h3>General</h3>
          <p>Appearance and local service configuration.</p>
        </div>
      </div>

      <div className="settings-group-card">
        <div className="settings-row">
          <div className="settings-row__icon">
            <Palette size={16} />
          </div>

          <div className="settings-row__body">
            <strong>Theme</strong>
            <span>Choose your interface appearance.</span>
          </div>

          <div className="theme-switcher">
            <button
              type="button"
              className={`theme-switcher__option ${
                theme === "light"
                  ? "theme-switcher__option--active"
                  : ""
              }`}
              onClick={() => onSetTheme("light")}
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
              onClick={() => onSetTheme("dark")}
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
              onClick={() => onSetTheme("system")}
            >
              <Settings size={15} />
              System
            </button>
          </div>
        </div>

        <div className="settings-row">
          <div className="settings-row__icon">
            {backendOnline ? (
              <CheckCircle2 size={16} />
            ) : (
              <AlertCircle size={16} />
            )}
          </div>

          <div className="settings-row__body">
            <strong>Backend</strong>
            <span>Trajecta local agent service.</span>
          </div>

          <span
            className={`settings-status-pill ${
              backendOnline
                ? "settings-status-pill--online"
                : "settings-status-pill--offline"
            }`}
          >
            {backendOnline ? "Connected" : "Disconnected"}
          </span>
        </div>
      </div>
    </section>
  );
}