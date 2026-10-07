import {
  Bug,
  FileSearch,
  ListTree,
  GitCompare,
} from "lucide-react";

import type {
  LucideIcon,
} from "lucide-react";

interface Suggestion {
  icon: LucideIcon;

  title: string;

  detail: string;

  prompt: string;
}

const SUGGESTIONS: Suggestion[] = [
  {
    icon: FileSearch,

    title: "Explore this project",

    detail:
      "Map the codebase and flag what matters.",

    prompt:
      "Give me a tour of this project: what it does, how it is organized, and which files matter most.",
  },

  {
    icon: Bug,

    title: "Debug a problem",

    detail:
      "Trace a failing test or runtime error.",

    prompt:
      "Help me debug an issue. Here is what I am seeing: ",
  },

  {
    icon: ListTree,

    title: "Plan a change",

    detail:
      "Turn a rough idea into concrete steps.",

    prompt:
      "Help me plan a change to this project. The goal is: ",
  },

  {
    icon: GitCompare,

    title: "Review the diff",

    detail:
      "Summarize and critique the current changes.",

    prompt:
      "Review the current git diff and tell me what looks risky or unfinished.",
  },
];

interface Props {
  disabled?: boolean;

  onSelect(prompt: string): void;
}

export function ChatWelcome({
  disabled,
  onSelect,
}: Props) {
  return (
    <div className="welcome">
      <div className="welcome__heading">
        <img
          className="welcome__mark"
          src="/icon.svg"
          alt=""
          aria-hidden="true"
        />

        <h1>
          How can I help?
        </h1>
      </div>

      <p>
        Ask Trajecta to inspect
        files, research, write,
        debug, or use its tools.
      </p>

      <div className="welcome__suggestions">
        {SUGGESTIONS.map(
          (suggestion) => {
            const Icon =
              suggestion.icon;

            return (
              <button
                key={
                  suggestion.title
                }
                className="welcome-card"
                type="button"
                disabled={disabled}
                onClick={() =>
                  onSelect(
                    suggestion.prompt,
                  )
                }
              >
                <Icon size={16} />

                <span className="welcome-card__title">
                  {suggestion.title}
                </span>

                <span className="welcome-card__detail">
                  {suggestion.detail}
                </span>
              </button>
            );
          },
        )}
      </div>
    </div>
  );
}
