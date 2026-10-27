"use client";

/**
 * Web Push, from the browser side.
 *
 * The server has had VAPID keys and a WebPushProvider since the backend was
 * built, and every notification so far has been recorded as `skipped` with
 * `no_subscription` — which is honest, but it means the one channel that
 * matters at 3 AM had never actually been exercised. This module is what turns
 * that channel on.
 *
 * Notes that matter:
 * - iOS only allows push for a PWA that has been added to the Home Screen, so
 *   `iosNeedsInstall()` drives an instruction instead of a broken button.
 * - The permission prompt must be triggered by a tap. Asking on page load is
 *   how apps get permanently denied by a user who never saw the question.
 */

export type PushStatus =
  | "unsupported"
  | "ios-install"
  | "default"
  | "denied"
  | "granted-unsubscribed"
  | "subscribed";

function urlBase64ToUint8Array(base64String: string): Uint8Array {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(base64);
  const out = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i += 1) out[i] = raw.charCodeAt(i);
  return out;
}

function isIos(): boolean {
  if (typeof navigator === "undefined") return false;
  return /iPad|iPhone|iPod/.test(navigator.userAgent);
}

function isStandalone(): boolean {
  if (typeof window === "undefined") return false;
  return (
    window.matchMedia("(display-mode: standalone)").matches ||
    // iOS Safari predates the display-mode media query for this.
    (window.navigator as unknown as { standalone?: boolean }).standalone === true
  );
}

export function iosNeedsInstall(): boolean {
  return isIos() && !isStandalone();
}

export function pushSupported(): boolean {
  return (
    typeof window !== "undefined" &&
    "serviceWorker" in navigator &&
    "PushManager" in window &&
    "Notification" in window
  );
}

export async function pushStatus(): Promise<PushStatus> {
  if (iosNeedsInstall()) return "ios-install";
  if (!pushSupported()) return "unsupported";
  if (Notification.permission === "denied") return "denied";
  try {
    const reg = await navigator.serviceWorker.getRegistration("/");
    const sub = await reg?.pushManager.getSubscription();
    if (sub) return "subscribed";
  } catch {
    /* fall through to permission state */
  }
  return Notification.permission === "granted" ? "granted-unsubscribed" : "default";
}

/**
 * Ask, subscribe, and tell the backend. Returns the resulting status so the
 * caller can render the real state rather than assuming success.
 */
export async function enablePush(memberId = "daughter"): Promise<PushStatus> {
  if (!pushSupported()) return iosNeedsInstall() ? "ios-install" : "unsupported";

  const permission = await Notification.requestPermission();
  if (permission !== "granted") return permission === "denied" ? "denied" : "default";

  const reg =
    (await navigator.serviceWorker.getRegistration("/")) ??
    (await navigator.serviceWorker.register("/sw.js", { scope: "/" }));
  await navigator.serviceWorker.ready;

  const keyRes = await fetch("/api/push/public-key", { cache: "no-store" });
  if (!keyRes.ok) throw new Error("Could not get the notification key from the house.");
  const { key, enabled } = (await keyRes.json()) as { key: string; enabled: boolean };
  if (!enabled) throw new Error("Notifications are not configured on this server.");

  const existing = await reg.pushManager.getSubscription();
  const sub =
    existing ??
    (await reg.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToUint8Array(key) as BufferSource,
    }));

  const res = await fetch("/api/push/subscribe", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ ...sub.toJSON(), member_id: memberId }),
  });
  if (!res.ok) throw new Error("The house did not accept this device.");

  return "subscribed";
}

export async function disablePush(): Promise<void> {
  if (!pushSupported()) return;
  const reg = await navigator.serviceWorker.getRegistration("/");
  const sub = await reg?.pushManager.getSubscription();
  if (!sub) return;
  // Unsubscribe locally first: a server that is down must not leave the phone
  // subscribed to alerts the family has just turned off.
  await sub.unsubscribe();
}
