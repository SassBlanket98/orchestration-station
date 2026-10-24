import type { Density, Theme } from "./settings";
import { nextRadioIndex } from "./navigation";

const choices = [
  { group: "Density", values: ["comfortable", "compact"] },
  { group: "Theme", values: ["dark", "system", "light"] },
] as const;

export function SettingsMenu({
  density,
  theme,
  onDensity,
  onTheme,
}: {
  density: Density;
  theme: Theme;
  onDensity: (value: Density) => void;
  onTheme: (value: Theme) => void;
}) {
  return (
    <div className="shell-popover" role="group" aria-label="Display settings">
      <h3>Display settings</h3>
      {choices.map(({ group, values }) => (
        <section key={group}>
          <div className="shell-popover__label" id={`settings-${group.toLowerCase()}-label`}>{group}</div>
          <div role="radiogroup" aria-labelledby={`settings-${group.toLowerCase()}-label`} onKeyDown={(event) => {
            const radios = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="radio"]'));
            const current = radios.indexOf(event.target as HTMLButtonElement);
            const next = nextRadioIndex(current, radios.length, event.key);
            if (next !== null) {
              event.preventDefault();
              radios[next]?.focus();
              radios[next]?.click();
            }
          }}>
          {values.map((value) => {
            const selected = group === "Density" ? density === value : theme === value;
            return (
              <button
                className="shell-menu-row"
                key={value}
                type="button"
                role="radio"
                aria-checked={selected}
                tabIndex={selected ? 0 : -1}
                onClick={() => group === "Density"
                  ? onDensity(value as Density)
                  : onTheme(value as Theme)}
              >
                <span aria-hidden="true">{selected ? "●" : "○"}</span>
                {value[0].toUpperCase() + value.slice(1)}
              </button>
            );
          })}
          </div>
        </section>
      ))}
    </div>
  );
}
