import type { ReactElement } from "react";
import type { MonitorStage } from "../../api/types";
import type { Activity } from "./RollupRows";
import { plainText, projectName } from "./RollupRows";
import { formatAge } from "./treeText";

/** Sessions waiting on the user, longest wait first, pinned above the tree. Nothing when nobody waits. */
export function NeedsYou({ stages, activity, nowSec, onOpen }: {
  stages: MonitorStage[];
  activity: Record<string, Activity>;
  nowSec: number;
  onOpen: (stage: MonitorStage) => void;
}): ReactElement | null {
  if (!stages.length) return null;
  const sorted = [...stages].sort((a, b) => a.last_activity - b.last_activity);
  return (
    <section className="needs-you" aria-label={`${stages.length} sessions need you`}>
      <h2 className="needs-head"><span>needs you</span><span className="needs-count">{stages.length}</span></h2>
      <ul className="needs-list">
        {sorted.map((stage) => {
          const act = activity[stage.path];
          const name = stage.display_name || stage.session_name || stage.label;
          return (
            <li key={stage.path}>
              <button type="button" className="needs-row" onClick={() => onOpen(stage)}>
                <span className="needs-glyph" aria-hidden="true">◐</span>
                <span className="needs-project">{projectName(stage.project || stage.label)}</span>
                <span className="needs-name">{name}</span>
                <span className="needs-wait">waiting {formatAge(Math.max(0, nowSec - stage.last_activity))}</span>
                {act ? <span className={`needs-act${act.kind === "ask" ? " ask" : ""}`}>{plainText(act.text)}</span> : null}
              </button>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
