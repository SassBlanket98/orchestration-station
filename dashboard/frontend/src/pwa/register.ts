let registered = false;

/**
 * Registers the service worker at `/sw.js`. Production builds only, and only once per page.
 * Success is silent; a failure is reported as a warning and the app keeps working online.
 */
export function registerServiceWorker(): void {
  if (!import.meta.env.PROD || registered) return;
  if (typeof navigator === "undefined" || !("serviceWorker" in navigator)) return;
  registered = true;
  window.addEventListener(
    "load",
    () => {
      navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch((error: unknown) => {
        console.warn("service worker registration failed", error);
      });
    },
    { once: true },
  );
}
