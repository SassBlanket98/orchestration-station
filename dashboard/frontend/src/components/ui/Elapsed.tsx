import { useEffect, useState } from "react";
import { formatElapsed } from "../../lib/format";

/** Duration from `from` to `to` (or now). Ticks while the end is open. */
export function Elapsed({
  from,
  to = null,
}: {
  from: number | null;
  to?: number | null;
}) {
  const [, force] = useState(0);
  useEffect(() => {
    if (to !== null) return;
    const id = window.setInterval(() => force((n) => n + 1), 1000);
    return () => window.clearInterval(id);
  }, [to]);
  return <span className="elapsed">{formatElapsed(from, to)}</span>;
}
