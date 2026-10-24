import { useState } from "react";
import type { ExtraLog } from "../../api/types";
import { Panel, TimeAgo } from "../ui";

/** Collapsed list of non-worker logs. Clicking an entry opens it in the log viewer. */
export function ExtraLogsPanel({
  logs,
  openStem,
  onOpen,
}: {
  logs: ExtraLog[];
  openStem: string | null;
  onOpen: (stem: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);

  return (
    <Panel
      title="extra logs"
      right={
        <button
          type="button"
          className="extra-logs__toggle"
          aria-expanded={expanded}
          onClick={() => setExpanded((v) => !v)}
        >
          {expanded ? "hide" : `show ${logs.length}`}
        </button>
      }
    >
      {expanded &&
        (logs.length === 0 ? (
          <span className="faint">none</span>
        ) : (
          <ul className="extra-logs">
            {logs.map((log) => (
              <li key={log.name}>
                <button
                  type="button"
                  className="extra-logs__entry"
                  aria-pressed={openStem === log.name}
                  onClick={() => onOpen(log.name)}
                >
                  <span className="mono extra-logs__name">{log.name}</span>
                  <span className="muted extra-logs__kind">{log.kind}</span>
                  <span className="extra-logs__last">{log.last_line ?? ""}</span>
                  <TimeAgo at={log.mtime} />
                </button>
              </li>
            ))}
          </ul>
        ))}
    </Panel>
  );
}
