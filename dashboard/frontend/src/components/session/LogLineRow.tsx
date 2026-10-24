import type { LogLine } from "../../api/types";
import { presentLogLine } from "./logPresentation";

/** One log line. `fresh` lines fade in once; there is no per-line layout motion. */
export function LogLineRow({ line, fresh }: { line: LogLine; fresh: boolean }) {
  const { dataKind, kindLabel, rowClass, showTool } = presentLogLine(line);
  const cls = fresh ? `${rowClass} log-line--fresh` : rowClass;

  return (
    <div className={cls} data-kind={dataKind}>
      <span className="log-line__kind">{kindLabel}</span>
      <span className="log-line__tool">{showTool ? line.tool : ""}</span>
      <span className="log-line__text">{line.text}</span>
    </div>
  );
}
