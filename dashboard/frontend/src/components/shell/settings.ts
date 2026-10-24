export type Density = "comfortable" | "compact";
export type Theme = "dark" | "system" | "light";

export const DENSITY_KEY = "stagewatch:density";
export const THEME_KEY = "stagewatch:theme";

export function browserStorage(): Pick<Storage, "getItem" | "setItem"> | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null;
  }
}

export function readPreference<T extends string>(
  storage: Pick<Storage, "getItem"> | null,
  key: string,
  fallback: T,
  allowed: readonly T[],
): T {
  try {
    const value = storage?.getItem(key);
    return value && allowed.includes(value as T) ? (value as T) : fallback;
  } catch {
    return fallback;
  }
}

export function writePreference(
  storage: Pick<Storage, "setItem"> | null,
  key: string,
  value: string,
): void {
  try {
    storage?.setItem(key, value);
  } catch {
    // Private browsing or storage policy can disable persistence.
  }
}

export function isMockMode(search: string): boolean {
  return new URLSearchParams(search).get("mock") === "1";
}
