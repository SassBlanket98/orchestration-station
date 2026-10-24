import type { WorkerStatus } from "../../api/types";
import { Pulse } from "./Pulse";

/** Worker status as a dot. Running workers get the animated ring. */
export function StatusDot({
  status,
  pulse,
}: {
  status: WorkerStatus;
  pulse?: boolean;
}) {
  if (pulse ?? status === "running") {
    return <Pulse active={status === "running"} title={status} />;
  }
  return <span className={`dot dot--${status}`} title={status} aria-label={status} />;
}
