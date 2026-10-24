/** Centred placeholder for an empty list or a missing session. */
export function EmptyState({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="empty-state">
      <span className="empty-state__title">{title}</span>
      {hint !== undefined && <span className="empty-state__hint">{hint}</span>}
    </div>
  );
}
