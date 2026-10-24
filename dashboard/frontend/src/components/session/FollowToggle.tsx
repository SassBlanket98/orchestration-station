/** Auto-scroll switch. Pauses itself when the user scrolls up; this resumes it. */
export function FollowToggle({
  following,
  onToggle,
}: {
  following: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      type="button"
      className={following ? "follow-toggle follow-toggle--on" : "follow-toggle"}
      aria-pressed={following}
      onClick={onToggle}
    >
      {following ? "follow: on" : "follow: paused"}
    </button>
  );
}
