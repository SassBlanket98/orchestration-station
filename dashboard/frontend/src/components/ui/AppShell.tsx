import type { ReactNode } from "react";
import { useEffect, useState } from "react";
import type { ConnState } from "../../state/types";
import { ShellHeader } from "../shell/ShellHeader";
import { browserStorage, DENSITY_KEY, readPreference, THEME_KEY, writePreference, type Density, type Theme } from "../shell/settings";

/** Top bar with brand, breadcrumb and connection badge, plus the page body. */
export function AppShell({
  conn = "live",
  breadcrumb,
  children,
}: {
  conn?: ConnState;
  breadcrumb?: ReactNode;
  children: ReactNode;
}) {
  const [density, setDensityState] = useState<Density>(() => readPreference(
    browserStorage(),
    DENSITY_KEY,
    "comfortable",
    ["comfortable", "compact"],
  ));
  const [theme, setThemeState] = useState<Theme>(() => readPreference(
    browserStorage(),
    THEME_KEY,
    "dark",
    ["dark", "system", "light"],
  ));
  const setDensity = (value: Density) => {
    setDensityState(value);
    writePreference(browserStorage(), DENSITY_KEY, value);
  };
  const setTheme = (value: Theme) => {
    setThemeState(value);
    writePreference(browserStorage(), THEME_KEY, value);
  };
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    document.documentElement.dataset.density = density;
  }, [density, theme]);
  return (
    <div className={`app-shell${density === "compact" ? " shell-density-compact" : ""}`}>
      <ShellHeader density={density} setDensity={setDensity} theme={theme} setTheme={setTheme} conn={conn}/>
      {breadcrumb !== undefined && <div hidden>{breadcrumb}</div>}
      <main className="app-shell__body">
        {children}
      </main>
    </div>
  );
}

/** A breadcrumb separator for callers building their own crumb trail. */
export function CrumbSep() {
  return <span className="app-shell__crumb-sep">/</span>;
}
