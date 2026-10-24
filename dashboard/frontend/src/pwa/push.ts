export type PushState = "unsupported" | "denied" | "off" | "on";

export function pushSupported(): boolean {
  return typeof Notification !== "undefined"
    && typeof PushManager !== "undefined"
    && "serviceWorker" in navigator;
}

export async function pushState(): Promise<PushState> {
  if (typeof Notification === "undefined") return "unsupported";
  if (Notification.permission === "denied") return "denied";
  if (!pushSupported()) return "unsupported";
  const registration = await navigator.serviceWorker.ready;
  return await registration.pushManager.getSubscription() ? "on" : "off";
}

function applicationServerKey(value: string): Uint8Array<ArrayBuffer> {
  const padded = value.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(value.length / 4) * 4, "=");
  const raw = atob(padded);
  const bytes = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i += 1) bytes[i] = raw.charCodeAt(i);
  return bytes;
}

async function requireOk(response: Response): Promise<void> {
  if (!response.ok) throw new Error(`Push request failed (${response.status})`);
}

export async function enablePush(): Promise<void> {
  if (!pushSupported()) throw new Error("Push is unsupported");
  const permission = await Notification.requestPermission();
  if (permission !== "granted") throw new Error("Notification permission denied");
  const keyResponse = await fetch("/api/push/key");
  await requireOk(keyResponse);
  const { publicKey } = await keyResponse.json() as { publicKey: string };
  const registration = await navigator.serviceWorker.ready;
  const subscription = await registration.pushManager.subscribe({
    userVisibleOnly: true,
    applicationServerKey: applicationServerKey(publicKey),
  });
  const response = await fetch("/api/push/subscribe", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(subscription.toJSON()),
  });
  await requireOk(response);
}

export async function disablePush(): Promise<void> {
  if (!pushSupported()) return;
  const registration = await navigator.serviceWorker.ready;
  const subscription = await registration.pushManager.getSubscription();
  if (!subscription) return;
  const response = await fetch("/api/push/unsubscribe", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ endpoint: subscription.endpoint }),
  });
  await requireOk(response);
  await subscription.unsubscribe();
}
