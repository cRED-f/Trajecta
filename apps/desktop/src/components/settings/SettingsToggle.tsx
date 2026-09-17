interface Props {
  checked: boolean;
  disabled?: boolean;
  label: string;
  onChange(checked: boolean): void;
}

export function SettingsToggle({
  checked,
  disabled = false,
  label,
  onChange,
}: Props) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      className={`settings-toggle ${
        checked ? "settings-toggle--on" : ""
      }`}
      onClick={() => onChange(!checked)}
    >
      <span className="settings-toggle__thumb" />
    </button>
  );
}