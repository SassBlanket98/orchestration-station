// Pure text helpers for the board tree: box-drawing prefixes, level tags, the NOW sub-line and ages.
// Ported from the Monitor ops console (opstree.py `_ot_prefixes`, `_ot_tag`; opsgrid.py `_og_now_text`, `_og_age`).

export const LEVEL_TAGS = ["LEAD", "SPAWN", "SUB", "WORKER", "CRON"] as const;
export type LevelTag = (typeof LEVEL_TAGS)[number];
export type LevelTone = "sub" | "violet" | "teal" | "dim" | "amber";

const TAG_SHORT: Record<LevelTag, string> = { LEAD: "LEAD", SPAWN: "SPWN", SUB: "SUB", WORKER: "WRKR", CRON: "CRON" };
const TAG_TONE: Record<LevelTag, LevelTone> = { LEAD: "sub", SPAWN: "violet", SUB: "teal", WORKER: "dim", CRON: "amber" };

const isLevelTag = (tag: string): tag is LevelTag => (LEVEL_TAGS as readonly string[]).includes(tag);

export interface TreePrefix {
  /** Text before the node's label: `├─ ` / `└─ ` after one `│  ` or `   ` per open ancestor level. */
  prefix: string;
  /** Same width as `prefix`, for the node's sub-lines: `│  ` where a later sibling follows, else spaces. */
  cont: string;
}

/**
 * For nodes listed in tree order with their depth (0 = a child of the project heading), each node's prefix and
 * continuation. A depth that jumps deeper than one level counts as one level (the first node is always level 0).
 */
export function treePrefixes(depths: number[]): TreePrefix[] {
  const levels: number[] = [];
  let prev = -1;
  for (const d of depths) {
    prev = Math.max(0, Math.min(d, prev + 1)); // a jump deeper than one level counts as one level
    levels.push(prev);
  }
  const n = levels.length;
  // last[i]: no later sibling under the same parent (the next node at or above its level is shallower, or none).
  const last: boolean[] = new Array<boolean>(n).fill(true);
  for (let i = 0; i < n; i++) {
    const d = levels[i];
    for (let j = i + 1; j < n; j++) {
      if (levels[j] <= d) {
        last[i] = levels[j] < d;
        break;
      }
    }
  }
  const out: TreePrefix[] = [];
  const open: number[] = []; // open ancestor index at each level
  for (let i = 0; i < n; i++) {
    const d = levels[i];
    open.length = Math.min(open.length, d);
    const ancestors = open.map((a) => (last[a] ? "   " : "│  ")).join("");
    const kids = i + 1 < n && levels[i + 1] > d;
    const tail = !last[i] || kids ? "│  " : "   ";
    out.push({ prefix: ancestors + (last[i] ? "└─ " : "├─ "), cont: ancestors + tail });
    open.push(i);
  }
  return out;
}

/** The short level tag for a cell: LEAD, SPWN, SUB, WRKR, CRON. Unknown tags pass through unchanged. */
export function levelTagShort(tag: string): string {
  return isLevelTag(tag) ? TAG_SHORT[tag] : tag;
}

/** The colour role of a level tag, or null for an unknown or blank tag. */
export function levelTagTone(tag: string): LevelTone | null {
  return isLevelTag(tag) ? TAG_TONE[tag] : null;
}

export interface NowStage {
  section?: string;
  lead_state?: string;
  latest_milestone?: { ts: number | null; text: string } | null;
  level_tag?: string;
  lead_tools?: number | null;
}

export interface NowLine {
  text: string;
  /** Seconds since the milestone, or null when there is no timestamp or no milestone. */
  ageSec: number | null;
}

/**
 * The NOW sub-line of a stage. Null when the stage is not live (section "active", or lead_state "live"/"background").
 * A live stage shows its latest milestone with its age; with none, LEAD/SPAWN/CRON or blank-tagged stages get an
 * honest "no activity recorded"; other tags get null.
 */
export function nowLine(stage: NowStage, nowSec: number): NowLine | null {
  const live = stage.section === "active" || stage.lead_state === "live" || stage.lead_state === "background";
  if (!live) return null;
  const milestone = stage.latest_milestone;
  if (milestone && milestone.text) {
    // A zero timestamp means "no stamp" (as in the Python original), so it has no age.
    const ageSec = milestone.ts ? Math.max(0, nowSec - milestone.ts) : null;
    return { text: milestone.text, ageSec };
  }
  const tag = stage.level_tag ?? "";
  if (tag === "" || tag === "LEAD" || tag === "SPAWN" || tag === "CRON") {
    return { text: "no activity recorded", ageSec: null };
  }
  return null;
}

/** A coarse age: `40s`, `25m`, `1h 05m`, `3d`. Fractions truncate; negative or non-finite input reads as 0. */
export function formatAge(sec: number): string {
  const s = Number.isFinite(sec) ? Math.max(0, Math.trunc(sec)) : 0;
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ${String(Math.floor((s % 3600) / 60)).padStart(2, "0")}m`;
  return `${Math.floor(s / 86400)}d`;
}
