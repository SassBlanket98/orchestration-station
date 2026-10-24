import type { Finding } from "../../api/types";

/** P1 is the high band, P2 the medium band, anything else the low band. */
function priorityBand(priority: string): "high" | "medium" | "low" {
  if (priority === "P1") return "high";
  if (priority === "P2") return "medium";
  return "low";
}

/** One finding with a priority badge coloured by band. */
export function FindingRow({ finding }: { finding: Finding }) {
  return (
    <li className="finding-row" data-priority={priorityBand(finding.priority)}>
      <span className="finding-row__priority mono">{finding.priority}</span>
      <span className="finding-row__text">{finding.text}</span>
    </li>
  );
}
