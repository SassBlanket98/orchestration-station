import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { isMockMode } from "../api/client";
import { commandMockItems } from "../mocks/command";
import { useSessions } from "../state/hooks";
import { commandDestination, commandItems, commandOptionId, matchCommandItems, moveCommandIndex, renderedCommandItems } from "./command/model";
import "./command/palette.css";

export default function CommandPalette() {
  const { sessions, loading, error } = useSessions();
  const mock = isMockMode();
  const location = useLocation();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const returnFocusRef = useRef<HTMLElement | null>(null);
  const items = useMemo(() => mock ? commandMockItems : commandItems(sessions ?? []), [mock, sessions]);
  const results = useMemo(() => renderedCommandItems(matchCommandItems(items, query)), [items, query]);
  const grouped = useMemo(() => ({
    "Jump to": results.filter((item) => item.group === "Jump to"),
    Workers: results.filter((item) => item.group === "Workers"),
  }), [results]);

  const close = useCallback(() => {
    setOpen(false);
    setQuery("");
    requestAnimationFrame(() => returnFocusRef.current?.focus());
  }, []);

  useEffect(() => {
    const openPalette = () => {
      if (!open) returnFocusRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
      setOpen(true);
      setQuery("");
      setActive(0);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        openPalette();
      } else if (event.key === "Escape" && open) {
        event.preventDefault();
        close();
      }
    };
    window.addEventListener("dashboard:command", openPalette);
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("dashboard:command", openPalette);
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [close, open]);

  useEffect(() => {
    if (open) requestAnimationFrame(() => inputRef.current?.focus());
  }, [open]);

  useEffect(() => setActive((current) => Math.min(current, Math.max(0, results.length - 1))), [results.length]);

  const select = (index: number) => {
    const item = results[index];
    if (!item) return;
    navigate(commandDestination(item, location.search));
    close();
  };

  if (!open) return null;
  return (
    <div className="command-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) close(); }}>
      <section className="command-palette" role="dialog" aria-modal="true" aria-label="Command palette">
        <div className="command-input-row">
          <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="6"/><path d="m16 16 5 5"/></svg>
          <input
            ref={inputRef}
            role="combobox"
            aria-label="Search runs, workers and commands"
            aria-autocomplete="list"
            aria-expanded="true"
            aria-controls="command-results"
            aria-activedescendant={results[active] ? commandOptionId(results[active].id) : undefined}
            value={query}
            placeholder="Search runs and workers…"
            onChange={(event) => { setQuery(event.target.value); setActive(0); }}
            onKeyDown={(event) => {
              if (event.key === "ArrowDown") { event.preventDefault(); setActive((index) => moveCommandIndex(index, 1, results.length)); }
              if (event.key === "ArrowUp") { event.preventDefault(); setActive((index) => moveCommandIndex(index, -1, results.length)); }
              if (event.key === "Enter") { event.preventDefault(); select(active); }
            }}
          />
          <kbd>ESC</kbd>
        </div>
        <div id="command-results" className="command-results" role="listbox" aria-label="Search results">
          {!mock && loading && <p className="command-empty" role="status">Loading runs and workers…</p>}
          {!mock && error && <p className="command-empty" role="alert">Could not load runs and workers.</p>}
          {(Object.entries(grouped) as ["Jump to" | "Workers", typeof results][]).map(([group, groupItems]) => groupItems.length > 0 && (
            <div className="command-group" key={group}>
              <div className="command-group-title">{group}</div>
              {groupItems.map((item) => {
                const index = results.findIndex((result) => result.id === item.id);
                return (
                  <Link
                    id={commandOptionId(item.id)}
                    key={item.id}
                    role="option"
                    tabIndex={-1}
                    aria-selected={active === index}
                    className={`command-option${active === index ? " is-active" : ""}`}
                    to={commandDestination(item, location.search)}
                    onMouseEnter={() => setActive(index)}
                    onClick={(event) => {
                      if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
                      event.preventDefault(); select(index);
                    }}
                  >
                    {item.engine === "codex" || item.engine === "cursor"
                      ? <span className={`command-engine command-engine--${item.engine}`} aria-label={item.engine === "codex" ? "Codex" : "Cursor"}>{item.engine === "codex" ? "CX" : "CU"}</span>
                      : <span className="command-arrow" aria-hidden="true">↗</span>}
                    <span className="command-label">{item.label}</span>
                    <span className={`command-detail command-status--${item.status}`}>{item.detail}</span>
                  </Link>
                );
              })}
            </div>
          ))}
          {results.length === 0 && (mock || (!loading && !error)) && <p className="command-empty">No matching runs or workers</p>}
        </div>
        <footer className="command-footer"><span>↑↓ navigate</span><span>↵ open</span><span>ESC close</span></footer>
      </section>
    </div>
  );
}
