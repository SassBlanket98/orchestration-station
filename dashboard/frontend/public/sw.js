/* The Station Dashboard service worker. Plain JS, no build step.
 *
 * - Navigations: network first; on failure the cached "/" shell (index.html).
 * - /assets/*: cache first (hashed files never change).
 * - Monitor, quota and session lists: network first with a timeout; on failure the last
 *   good copy, marked with X-Board-Stale (epoch ms when it was cached).
 * Everything else, including non-GET requests, SSE and /api/*\/stream, goes to the network.
 */

const VERSION = "v2";
const CACHE_PREFIX = "board-";
const SHELL_CACHE = `${CACHE_PREFIX}shell-${VERSION}`;
const DATA_CACHE = `${CACHE_PREFIX}data-${VERSION}`;
const CURRENT_CACHES = new Set([SHELL_CACHE, DATA_CACHE]);

const SHELL_KEY = "/";
const DATA_PATHS = new Set(["/api/monitor", "/api/quota/usage", "/api/sessions"]);
const NETWORK_TIMEOUT_MS = 4000;
const STALE_HEADER = "X-Board-Stale";
const CACHED_AT_HEADER = "X-Board-Cached-At";

self.addEventListener("install", () => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    (async () => {
      const names = await caches.keys();
      await Promise.all(
        names
          .filter((name) => name.startsWith(CACHE_PREFIX) && !CURRENT_CACHES.has(name))
          .map((name) => caches.delete(name)),
      );
      await self.clients.claim();
    })(),
  );
});

function isAuthPath(pathname) {
  return pathname === "/login" || pathname.startsWith("/auth/");
}

function isStreamPath(pathname) {
  return pathname === "/api/events" || (pathname.startsWith("/api/") && pathname.endsWith("/stream"));
}

// Only a plain 200 that was not redirected (for example to /login) is safe to keep.
function cacheableShell(response) {
  if (response.status !== 200 || response.redirected || response.type !== "basic") return false;
  return !isAuthPath(new URL(response.url).pathname);
}

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (isStreamPath(url.pathname)) return;

  // Signing out (or landing on the login page) drops cached dashboard data from this browser.
  if (request.mode === "navigate" && isAuthPath(url.pathname)) {
    event.waitUntil(caches.delete(DATA_CACHE));
  }
  if (request.mode === "navigate") {
    event.respondWith(handleNavigation(event, request));
    return;
  }
  if (url.pathname.startsWith("/assets/")) {
    event.respondWith(handleAsset(request));
    return;
  }
  if (DATA_PATHS.has(url.pathname)) {
    event.respondWith(handleData(event, request));
  }
});

self.addEventListener("push", (event) => {
  let payload = {};
  try {
    payload = event.data ? event.data.json() : {};
  } catch {
    payload = { body: event.data?.text() ?? "" };
  }
  const title = payload.title ?? "Station Dashboard";
  const options = {
    body: payload.body ?? "",
    tag: payload.tag,
    icon: "/icons/icon-192.png",
    badge: "/icons/icon-192.png",
    data: { url: "/", badge: payload.badge },
  };
  event.waitUntil((async () => {
    await self.registration.showNotification(title, options);
    if (typeof self.navigator.setAppBadge === "function" && Number.isFinite(payload.badge)) {
      await self.navigator.setAppBadge(payload.badge);
    }
  })());
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  event.waitUntil((async () => {
    const windows = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    if (windows.length > 0) {
      await windows[0].focus();
      return;
    }
    await self.clients.openWindow(event.notification.data?.url ?? "/");
  })());
});

async function handleNavigation(event, request) {
  try {
    const response = await fetch(request);
    if (!isAuthPath(new URL(request.url).pathname) && cacheableShell(response)) {
      event.waitUntil(
        caches.open(SHELL_CACHE).then((cache) => cache.put(SHELL_KEY, response.clone())),
      );
    }
    return response;
  } catch (error) {
    const cached = await caches.open(SHELL_CACHE).then((cache) => cache.match(SHELL_KEY));
    if (cached) return cached;
    throw error;
  }
}

async function handleAsset(request) {
  const cache = await caches.open(SHELL_CACHE);
  const hit = await cache.match(request);
  if (hit) return hit;
  const response = await fetch(request);
  if (response.status === 200 && response.type === "basic") {
    await cache.put(request, response.clone());
  }
  return response;
}

// Aborts the network request after NETWORK_TIMEOUT_MS so a hung connection falls back to cache.
async function fetchWithTimeout(request, ms) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), ms);
  try {
    return await fetch(new Request(request, { signal: controller.signal }));
  } finally {
    clearTimeout(timer);
  }
}

async function handleData(event, request) {
  const url = new URL(request.url);
  const cache = await caches.open(DATA_CACHE);
  let response;
  try {
    response = await fetchWithTimeout(request, NETWORK_TIMEOUT_MS);
  } catch (error) {
    const stored = await cache.match(request.url);
    if (stored) return withStaleHeader(stored);
    throw error;
  }

  // 5xx counts as a failure: serve the last good copy if there is one.
  if (response.status >= 500) {
    const stored = await cache.match(request.url);
    if (stored) return withStaleHeader(stored);
    return response;
  }
  // 200s are kept; 401 and other errors pass through and are never cached.
  // An "offline" answer from the Monitor proxy is HTTP 200 too; never let it replace the last good copy.
  const offlineAnswer = url.pathname === "/api/monitor"
    && (await response.clone().json().then((body) => body && body.online === false, () => false));
  if (response.status === 200 && !offlineAnswer) {
    event.waitUntil(storeData(cache, request.url, response.clone()));
  }
  return response;
}

async function storeData(cache, key, response) {
  const body = await response.arrayBuffer();
  const headers = new Headers(response.headers);
  headers.set(CACHED_AT_HEADER, String(Date.now()));
  await cache.put(key, new Response(body, { status: 200, headers }));
}

// Copy of a stored data response with the stale marker. The cached-at stamp is internal only.
function withStaleHeader(stored) {
  const headers = new Headers(stored.headers);
  const cachedAt = headers.get(CACHED_AT_HEADER) ?? "0";
  headers.delete(CACHED_AT_HEADER);
  headers.set(STALE_HEADER, cachedAt);
  return stored.arrayBuffer().then(
    (body) => new Response(body, { status: stored.status, statusText: stored.statusText, headers }),
  );
}
