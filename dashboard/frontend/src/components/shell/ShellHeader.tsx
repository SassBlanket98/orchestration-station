import { useEffect, useRef, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { isMockMode as queryIsMockMode } from "../../api/client";
import { shellMock } from "../../mocks/shell";
import type { Density, Theme } from "./settings";
import { SettingsMenu } from "./SettingsMenu";
import type { ConnState } from "../../state/types";
import { useSessions } from "../../state/hooks";
import AlertsMenu from "../AlertsMenu";
import { isTabActive, runsTarget, withSearch } from "./navigation";

function Icon({ name }: { name: "search" | "bell" | "settings" }) {
  const paths = {
    search: <><circle cx="11" cy="11" r="6"/><path d="m16 16 5 5"/></>,
    bell: <><path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9ZM10 21h4"/></>,
    settings: <><circle cx="12" cy="12" r="3"/><path d="M12 2v3m0 14v3M2 12h3m14 0h3M5 5l2 2m10 10 2 2M19 5l-2 2M7 17l-2 2"/></>,
  };
  return <svg viewBox="0 0 24 24" aria-hidden="true">{paths[name]}</svg>;
}

export function ShellHeader({ density, setDensity, theme, setTheme, conn = "live" }: {
  density: Density;
  setDensity: (value: Density) => void;
  theme: Theme;
  setTheme: (value: Theme) => void;
  conn?: ConnState;
}) {
  const [open, setOpen] = useState<"alerts" | "settings" | null>(null);
  const headerRef = useRef<HTMLElement>(null);
  const triggers = useRef<Record<string, HTMLButtonElement | null>>({});
  const location = useLocation();
  const mock = queryIsMockMode();
  const { sessions } = useSessions();
  const target = runsTarget(sessions, mock);
  // Plain Link, not NavLink: NavLink drops an explicit aria-current when its own match is false,
  // so /runs/:sid/* lost the highlight. aria-current drives the tab styling in global.css.
  const tab = (prefix: string) => (isTabActive(prefix, location.pathname) ? "page" : undefined);
  const toggle = (value: "alerts" | "settings") => setOpen((current) => current === value ? null : value);
  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") { setOpen(null); triggers.current[open]?.focus(); }
    };
    const onPointerDown = (event: PointerEvent) => {
      if (event.target instanceof Node && !headerRef.current?.contains(event.target)) setOpen(null);
    };
    document.addEventListener("keydown", onKeyDown);
    document.addEventListener("pointerdown", onPointerDown);
    return () => { document.removeEventListener("keydown", onKeyDown); document.removeEventListener("pointerdown", onPointerDown); };
  }, [open]);
  return (
    <header className="app-shell__bar" ref={headerRef}>
      <Link to={withSearch("/", location.search)} className="app-shell__brand" aria-label="Station Dashboard home">station dashboard</Link>
      <nav className="shell-nav" aria-label="Primary navigation">
        <Link to={withSearch("/", location.search)} aria-current={tab("/")}>Overview</Link>
        <Link to={target ? withSearch(target, location.search) : "#"} aria-current={tab("/runs")} aria-disabled={!target || undefined} title={!target ? "No runs" : undefined} onClick={(event) => { if (!target) event.preventDefault(); }}>Runs</Link>
        <Link to={withSearch("/quota", location.search)} aria-current={tab("/quota")}>Quota</Link>
        <Link to={withSearch("/logs", location.search)} aria-current={tab("/logs")}>Logs</Link>
      </nav>
      <div className="shell-actions">
        <button type="button" className="shell-search" aria-label="Open command palette" onClick={() => window.dispatchEvent(new Event("dashboard:command"))}><Icon name="search"/><span>Search anything</span><kbd>⌘K</kbd></button>
        <span className={`shell-live shell-live--${conn}`}><i/>{conn === "live" ? "Live" : conn === "connecting" ? "Connecting" : "Reconnecting"}</span>
        <button ref={(node) => { triggers.current.alerts = node; }} type="button" className="shell-icon" aria-label="Alerts" aria-expanded={open === "alerts"} onClick={() => toggle("alerts")}><Icon name="bell"/>{mock && <span className="shell-badge">{shellMock.attentionCount}</span>}</button>
        <button ref={(node) => { triggers.current.settings = node; }} type="button" className="shell-icon shell-settings-trigger" aria-label="Display settings" aria-expanded={open === "settings"} onClick={() => toggle("settings")}><Icon name="settings"/></button>
        {mock && <span className="shell-illus">ILLUSTRATIVE DATA</span>}
      </div>
      {open === "settings" && <SettingsMenu density={density} theme={theme} onDensity={setDensity} onTheme={setTheme}/>}
      {open === "alerts" && <AlertsMenu open onClose={() => setOpen(null)}/>}
    </header>
  );
}
