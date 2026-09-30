import {
  AlertCircle,
  Network,
  Plus,
  RefreshCw,
  Unlink,
} from "lucide-react";

import { useState } from "react";

import {
  useSkillActions,
  useSkillDependencies,
} from "../../hooks/use-settings";

import { relativeTime } from "../../lib/format";

interface Props {
  skillName: string;

  /** Registered skill names, so a dependency can only target a real skill. */
  skillNames: string[];
}

function messageOf(error: unknown): string {
  return error instanceof Error
    ? error.message
    : "Something went wrong. Please try again.";
}

/** Declared edges of the skill dependency graph for one skill. */
export function SkillDependencies({ skillName, skillNames }: Props) {
  const query = useSkillDependencies(skillName);
  const actions = useSkillActions();

  const [target, setTarget] = useState("");
  const [constraint, setConstraint] = useState("");
  const [required, setRequired] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const dependencies = query.data ?? [];
  const declared = new Set(dependencies.map((item) => item.depends_on_skill));

  const options = skillNames.filter(
    (name) => name !== skillName && !declared.has(name),
  );

  async function addDependency() {
    if (!target) {
      return;
    }

    setError(null);

    try {
      await actions.addDependency({
        skillName,
        body: {
          depends_on_skill: target,
          version_constraint: constraint.trim() || undefined,
          required,
        },
      });

      setTarget("");
      setConstraint("");
    } catch (caught) {
      setError(messageOf(caught));
    }
  }

  async function removeDependency(dependsOnSkill: string) {
    setError(null);

    try {
      await actions.removeDependency({
        skillName,
        dependsOnSkill,
      });
    } catch (caught) {
      setError(messageOf(caught));
    }
  }

  return (
    <div className="skill-dependencies">
      <div className="settings-subsection__heading-row">
        <h6 className="settings-subsection__heading">Dependencies</h6>

        {dependencies.length > 0 && (
          <span className="settings-subsection__count">
            {dependencies.length}
          </span>
        )}
      </div>

      {query.isLoading && (
        <div className="settings-loading">
          <RefreshCw className="settings-spin" size={16} />
          Loading dependencies…
        </div>
      )}

      {query.isError && (
        <div className="settings-error-card">
          <AlertCircle size={16} />

          <span>{messageOf(query.error)}</span>
        </div>
      )}

      {error && (
        <div className="settings-error-card">
          <AlertCircle size={16} />

          <span>{error}</span>
        </div>
      )}

      {!query.isLoading && !query.isError && dependencies.length === 0 && (
        <p className="skill-analytics-note">
          This skill declares no dependencies.
        </p>
      )}

      {dependencies.length > 0 && (
        <ul className="skill-dependency-list">
          {dependencies.map((dependency) => (
            <li className="skill-dependency" key={dependency.depends_on_skill}>
              <div className="skill-dependency__main">
                <span className="skill-dependency__name">
                  <Network size={13} />
                  {dependency.depends_on_skill}
                </span>

                <span className="skill-dependency__meta">
                  {dependency.version_constraint
                    ? `needs ${dependency.version_constraint}`
                    : "any version"}
                  {" · "}
                  {dependency.required ? "required" : "optional"}
                  {" · "}
                  {relativeTime(dependency.created_at)}
                </span>
              </div>

              <button
                type="button"
                className="settings-text-button settings-text-button--danger"
                disabled={actions.removingDependency}
                onClick={() =>
                  void removeDependency(dependency.depends_on_skill)
                }
              >
                <Unlink size={13} />
                Remove
              </button>
            </li>
          ))}
        </ul>
      )}

      {options.length > 0 && (
        <div className="skill-dependency-form">
          <label className="sr-only" htmlFor={`dependency-of-${skillName}`}>
            Depends on
          </label>

          <select
            id={`dependency-of-${skillName}`}
            value={target}
            onChange={(event) => setTarget(event.target.value)}
          >
            <option value="">Depends on…</option>

            {options.map((name) => (
              <option value={name} key={name}>
                {name}
              </option>
            ))}
          </select>

          <label className="sr-only" htmlFor={`constraint-of-${skillName}`}>
            Version constraint
          </label>

          <input
            id={`constraint-of-${skillName}`}
            type="text"
            value={constraint}
            placeholder="e.g. >=1.0.0"
            onChange={(event) => setConstraint(event.target.value)}
          />

          <label className="skill-dependency-form__check">
            <input
              type="checkbox"
              checked={required}
              onChange={(event) => setRequired(event.target.checked)}
            />
            Required
          </label>

          <button
            type="button"
            className="settings-text-button"
            disabled={!target || actions.addingDependency}
            onClick={() => void addDependency()}
          >
            {actions.addingDependency ? (
              <RefreshCw className="settings-spin" size={13} />
            ) : (
              <Plus size={13} />
            )}
            Add
          </button>
        </div>
      )}

      {!query.isLoading && options.length === 0 && dependencies.length > 0 && (
        <p className="skill-analytics-note">
          Every other registered skill is already a dependency.
        </p>
      )}
    </div>
  );
}
