import {
  Moon,
  Settings,
  Sun,
} from "lucide-react";

import {
  useEffect,
  useRef,
} from "react";

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

export function SettingsModal({
  open,
  theme,
  backendOnline,
  onClose,
  onSetTheme,
}: Props) {
  const overlayRef =
    useRef<HTMLDivElement>(null);

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

  if (!open) return null;

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
      <div className="settings-modal">
        <div className="settings-modal__header">
          <h2>
            Settings
          </h2>
        </div>

        <div className="settings-modal__body">
          <div className="settings-section">
            <div className="settings-section__label">
              General
            </div>

            <div className="settings-item">
              <span className="settings-item__label">
                Theme
              </span>

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
                  <Sun
                    size={16}
                  />
                  <span>
                    Light
                  </span>
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
                  <Moon
                    size={16}
                  />
                  <span>
                    Dark
                  </span>
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
                    size={16}
                  />
                  <span>
                    System
                  </span>
                </button>
              </div>
            </div>

            <div className="settings-item">
              <span className="settings-item__label">
                Backend
              </span>

              <span
                className={`settings-item__status ${
                  backendOnline
                    ? "settings-item__status--online"
                    : "settings-item__status--offline"
                }`}
              >
                {backendOnline
                  ? "Connected"
                  : "Disconnected"}
              </span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
