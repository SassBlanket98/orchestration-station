// Illustrative Attention list for ?mock=1, matching mockups/menus-1440.html.
import type { AlertItem } from "../components/alerts/derive";

const SESSION_ID = "run:L2hvbWUvdXNlci9Qcm9qZWN0cy9xdW90YS1zZXJ2aWNlLy5zdGFnZS9yYW0tMjAyNi0xMC0wOA";

/** Times are offsets from `nowS`, so the mock keeps reading as "recent" while open. */
export function alertsMockFixture(nowS: number = Date.now() / 1000): AlertItem[] {
  const ago = (minutes: number) => nowS - minutes * 60;
  return [
    {
      id: "idle:mock:audit-expanded-ui",
      kind: "idle",
      sessionId: SESSION_ID,
      text: "audit-expanded-ui · idle 4 m",
      at: ago(4),
      href: `/runs/${encodeURIComponent(SESSION_ID)}/workers/audit-expanded-ui`,
    },
    {
      id: "stdin_hang:mock:fix1",
      kind: "stdin_hang",
      sessionId: SESSION_ID,
      text: "fix1 attempt 1 · stdin hang",
      at: ago(17),
      href: `/runs/${encodeURIComponent(SESSION_ID)}/workers/fix1`,
    },
    {
      id: "review:mock",
      kind: "review",
      sessionId: SESSION_ID,
      text: "review-1 · 1 P1, 2 P2",
      at: ago(23),
      href: `/runs/${encodeURIComponent(SESSION_ID)}/reviews`,
    },
    {
      id: "exited:mock:wt-score",
      kind: "exited",
      sessionId: SESSION_ID,
      text: "wt-score · exited, no report",
      at: null,
      href: `/runs/${encodeURIComponent(SESSION_ID)}/workers/wt-score`,
    },
  ];
}
