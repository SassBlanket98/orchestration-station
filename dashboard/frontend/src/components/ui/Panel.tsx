import type { ReactNode } from "react";

/** Bordered surface with an optional header and a right-side slot. */
export function Panel({
  title,
  right,
  children,
  className,
}: {
  title?: ReactNode;
  right?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  const cls = className === undefined ? "panel" : `panel ${className}`;
  return (
    <section className={cls}>
      {(title !== undefined || right !== undefined) && (
        <header className="panel__head">
          {title !== undefined && <span className="panel__title">{title}</span>}
          {right !== undefined && <span className="panel__right">{right}</span>}
        </header>
      )}
      <div className="panel__body">{children}</div>
    </section>
  );
}
