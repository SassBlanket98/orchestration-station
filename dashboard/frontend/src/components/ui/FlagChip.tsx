/** A single worker flag, colour-coded by the `data-flag` attribute. */
export function FlagChip({ flag }: { flag: string }) {
  return (
    <span className="flag-chip" data-flag={flag}>
      {flag}
    </span>
  );
}
