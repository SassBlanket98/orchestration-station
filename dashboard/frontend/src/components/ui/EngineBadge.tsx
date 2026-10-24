import type { Engine } from "../../api/types";

/** The agent engine that runs a worker; independent of its model name. */
export function EngineBadge({ engine }: { engine: Engine }) {
  return <span className={`engine-badge engine-badge--${engine}`}>{engine}</span>;
}
