import {
  Check,
  ChevronDown,
} from "lucide-react";

import {
  useState,
} from "react";

interface Option {
  value: string;

  label: string;
}

interface Props {
  value: string;

  options: Option[];

  disabled?: boolean;

  onChange(
    value: string,
  ): void;
}

export function Dropdown({
  value,
  options,
  disabled,
  onChange,
}: Props) {
  const [open, setOpen] =
    useState(false);

  const selected =
    options.find(
      (option) =>
        option.value ===
        value,
    ) ?? options[0];

  return (
    <div className="dropdown">
      <button
        type="button"
        className="dropdown__trigger"
        disabled={disabled}
        title={selected?.label}
        onClick={() =>
          setOpen(
            (open) =>
              !open,
          )
        }
      >
        <span className="dropdown__value">
          {selected?.label ?? ""}
        </span>

        <ChevronDown
          size={14}
        />
      </button>

      {open && (
        <div className="dropdown__menu">
          {options.map((option) => (
            <button
              key={option.value}
              type="button"
              className={`dropdown__option ${
                option.value ===
                value
                  ? "dropdown__option--selected"
                  : ""
              }`}
              onClick={() => {
                onChange(option.value);

                setOpen(false);
              }}
            >
              {option.label}

              {option.value ===
                value && (
                <Check
                  size={14}
                />
              )}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
