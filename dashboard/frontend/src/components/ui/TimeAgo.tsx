import { useEffect, useState } from "react";
import { formatTimeAgo } from "../../lib/format";

/** Relative "N ago" label that refreshes once a second. */
export function TimeAgo({ at }: { at: number | null }) {
  const [, force] = useState(0);
  useEffect(() => {
    const id = window.setInterval(() => force((n) => n + 1), 1000);
    return () => window.clearInterval(id);
  }, []);
  return <span className="time-ago">{formatTimeAgo(at)}</span>;
}
