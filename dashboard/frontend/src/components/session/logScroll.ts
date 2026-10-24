// Scroll geometry for the log viewer's follow mode. Kept as a pure function so
// it can be tested without a DOM.

/** Pixels from the end that still count as "at the bottom". */
export const BOTTOM_SLACK_PX = 24;

/** True when the scroll position is within `slack` px of the end. */
export function isNearBottom(
  metrics: { scrollTop: number; clientHeight: number; scrollHeight: number },
  slack = BOTTOM_SLACK_PX,
): boolean {
  return metrics.scrollHeight - metrics.scrollTop - metrics.clientHeight <= slack;
}
