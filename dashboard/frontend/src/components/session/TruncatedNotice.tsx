/** Shown above a log when only its most recent window was loaded. */
export function TruncatedNotice({ truncated }: { truncated: boolean }) {
  if (!truncated) return null;
  return (
    <div className="truncated-notice" role="note">
      Showing the most recent output. Earlier lines were truncated.
    </div>
  );
}
