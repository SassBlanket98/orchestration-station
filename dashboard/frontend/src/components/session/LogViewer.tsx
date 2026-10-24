import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { useLogStream } from "../../state/hooks";
import { EmptyState } from "../ui";
import { FollowToggle } from "./FollowToggle";
import { LogLineRow } from "./LogLineRow";
import { TruncatedNotice } from "./TruncatedNotice";
import { logFormatLabel } from "./logPresentation";
import { isNearBottom } from "./logScroll";

/**
 * A worker or extra log. Follows the tail until the user scrolls up; the
 * FollowToggle resumes following and jumps to the end.
 */
export function LogViewer({
  sessionId,
  stem,
  live,
}: {
  sessionId: string;
  stem: string;
  live: boolean;
}) {
  const { lines, format, truncated } = useLogStream(sessionId, stem, live);
  const scrollRef = useRef<HTMLDivElement>(null);
  const [following, setFollowing] = useState(true);
  // Lines past this index arrived after the first batch, so only they fade in.
  const [baseline, setBaseline] = useState<number | null>(null);

  useEffect(() => {
    if (baseline === null && lines.length > 0) setBaseline(lines.length);
  }, [baseline, lines.length]);

  useLayoutEffect(() => {
    const el = scrollRef.current;
    if (following && el !== null) el.scrollTop = el.scrollHeight;
  }, [following, lines]);

  const onScroll = () => {
    const el = scrollRef.current;
    if (el !== null) setFollowing(isNearBottom(el));
  };

  return (
    <div className="log-viewer">
      <div className="log-viewer__bar">
        <span className="mono muted log-viewer__format">{logFormatLabel(format)}</span>
        <span className="mono faint">{lines.length} lines</span>
        <span className="log-viewer__spacer" />
        <FollowToggle following={following} onToggle={() => setFollowing((v) => !v)} />
      </div>
      <TruncatedNotice truncated={truncated} />
      {lines.length === 0 ? (
        <EmptyState
          title="No log output"
          hint={format === "missing" ? "No log file found." : "Nothing has been written yet."}
        />
      ) : (
        <div className="log-viewer__body" ref={scrollRef} onScroll={onScroll} tabIndex={0}>
          {lines.map((line, i) => (
            <LogLineRow key={i} line={line} fresh={baseline !== null && i >= baseline} />
          ))}
        </div>
      )}
    </div>
  );
}
