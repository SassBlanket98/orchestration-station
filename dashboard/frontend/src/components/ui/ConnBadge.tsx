import type { ConnState } from "../../state/types";

/** SSE connection indicator. Blinks while reconnecting. */
export function ConnBadge({ conn }: { conn: ConnState }) {
  return (
    <span className={`conn-badge conn-badge--${conn}`}>
      <span className="conn-badge__pip" />
      {conn}
    </span>
  );
}
