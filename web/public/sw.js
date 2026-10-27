/*
 * Aegis service worker.
 *
 * It exists for exactly one reason: a push notification has to arrive when the
 * app is not open. That is the whole product promise at 3 AM — the phone is on
 * a bedside table, not on the Today page.
 *
 * Deliberately no offline cache of incidents. A cached "all clear" shown from
 * stale storage would be a lie, and this is the one interface where a stale
 * answer is dangerous.
 */

const VERSION = "aegis-v1";

self.addEventListener("install", (event) => {
  // Nothing to precache. Take over immediately so the first push is not missed.
  event.waitUntil(self.skipWaiting());
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener("push", (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch {
    data = { title: "Aegis", body: event.data ? event.data.text() : "" };
  }

  const severity = data.severity || "watch";
  const phase = data.phase || 1;
  const title = data.title || "Aegis";
  const body = data.body || "";

  event.waitUntil(
    self.registration.showNotification(title, {
      body,
      // Same tag for both phases: the lock screen updates in place instead of
      // buzzing twice for one event. This is the difference between an alert
      // and a nuisance.
      tag: data.tag || data.incident_id || VERSION,
      renotify: phase === 1,
      requireInteraction: severity === "urgent" || severity === "emergency",
      silent: false,
      data: {
        url: data.url || (data.incident_id ? `/alerts/${data.incident_id}` : "/alerts"),
        incident_id: data.incident_id || null,
        severity,
      },
      icon: "/icon-192.png",
      badge: "/icon-192.png",
      timestamp: Date.now(),
    }),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const target = (event.notification.data && event.notification.data.url) || "/alerts";

  event.waitUntil(
    (async () => {
      const all = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
      // If the app is already open, focus it and navigate rather than stacking
      // a second copy of the same tab.
      for (const client of all) {
        if (new URL(client.url).origin === self.location.origin) {
          await client.focus();
          if ("navigate" in client) {
            try {
              await client.navigate(target);
            } catch {
              /* older clients cannot navigate; the focus is still useful */
            }
          }
          return;
        }
      }
      await self.clients.openWindow(target);
    })(),
  );
});

// Some browsers rotate subscriptions silently. Re-subscribing here is cheaper
// than discovering the silence at the worst possible moment.
self.addEventListener("pushsubscriptionchange", (event) => {
  event.waitUntil(
    (async () => {
      const old = event.oldSubscription;
      if (!old) return;
      const res = await fetch("/api/push/public-key");
      if (!res.ok) return;
      const { key } = await res.json();
      const sub = await self.registration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(key),
      });
      await fetch("/api/push/subscribe", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(sub.toJSON()),
      });
    })(),
  );
});

function urlBase64ToUint8Array(base64String) {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(base64);
  const out = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i += 1) out[i] = raw.charCodeAt(i);
  return out;
}
