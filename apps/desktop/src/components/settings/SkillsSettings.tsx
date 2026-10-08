/**
 * Skills is now the experience-first learning workspace.
 *
 * The former evaluation dashboard is deliberately not mounted: it polled
 * experiments, attached background evaluation streams, and encouraged replay
 * for every candidate. Advanced evaluation routes remain server-side for
 * explicitly initiated diagnostics; they are not the normal learning UI.
 */
import { AlertCircle } from "lucide-react";
import { ExperiencePanel } from "../skills/ExperiencePanel";

interface Props {
  enabled: boolean;
  backendOnline: boolean;
}

export function SkillsSettings({ enabled, backendOnline }: Props) {
  if (!backendOnline) {
    return (
      <div className="settings-empty-state">
        <AlertCircle size={22} />
        <strong>Backend disconnected</strong>
        <span>Reconnect Trajecta to view learned experience and saved skills.</span>
      </div>
    );
  }
  return <ExperiencePanel enabled={enabled && backendOnline} />;
}
