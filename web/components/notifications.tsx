"use client";

import { useCallback, useEffect, useState } from "react";
import { Banner } from "@/components/ui";
import {
  disablePush,
  enablePush,
  iosNeedsInstall,
  pushStatus,
  type PushStatus,
} from "@/lib/push";

/** Push subscription controls for the family device. */

const COPY: Record<PushStatus, { title: string; body: string; action?: string }> = {
  unsupported: {
    title: "This browser cannot receive alerts",
    body: "Try Chrome, Edge, or Safari on a phone. The dashboard still works here — you just will not be woken.",
  },
  "ios-install": {
    title: "Add Aegis to your Home Screen first",
    body: "On iPhone, notifications only work once the app is installed. Tap Share, then Add to Home Screen, then open it from there.",
  },
  default: {
    title: "Wake this phone",
    body: "Aegis will send a notification only when something needs you. Nothing else, ever.",
    action: "Turn on alerts",
  },
  denied: {
    title: "Alerts are blocked in this browser",
    body: "You said no to notifications for this site, so Aegis cannot wake you. Open the site settings in your browser, allow notifications, then reload this page.",
  },
  "granted-unsubscribed": {
    title: "Permission is on, but this device is not registered",
    body: "The browser will allow alerts but Aegis has no subscription from it. Register it now.",
    action: "Register this device",
  },
  subscribed: {
    title: "This phone will be woken",
    body: "Alerts arrive here even when the app is closed. Clips are never attached — the notification only says what happened.",
  },
};

export function Notifications({ memberId = "daughter" }: { memberId?: string }) {
  const [status, setStatus] = useState<PushStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [justOn, setJustOn] = useState(false);
  const [proved, setProved] = useState(false);

  const refresh = useCallback(async () => {
    try {
      setStatus(await pushStatus());
    } catch {
      setStatus("unsupported");
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const on = async () => {
    setBusy(true);
    setError(null);
    try {
      const next = await enablePush(memberId);
      setStatus(next);
      setJustOn(next === "subscribed");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not turn on alerts.");
      await refresh();
    } finally {
      setBusy(false);
    }
  };

  const off = async () => {
    setBusy(true);
    setError(null);
    try {
      await disablePush();
      setJustOn(false);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not turn off alerts.");
    } finally {
      setBusy(false);
    }
  };

  const prove = async () => {
    setBusy(true);
    setError(null);
    setJustOn(false);
    try {
      const res = await fetch("/api/push/test", { method: "POST" });
      const body = (await res.json()) as {
        ok: boolean;
        devices: number;
        error?: string;
      };
      if (!body.ok) {
        setError(
          body.devices === 0
            ? "No device is registered yet."
            : `The push service refused it: ${body.error || "unknown reason"}`,
        );
      } else {
        setProved(true);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not send the test.");
    } finally {
      setBusy(false);
    }
  };

  const copy = status ? COPY[status] : null;
  const canAsk = status === "default" || status === "granted-unsubscribed";

  return (
    <div className="card" style={{ padding: 20 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10 }}>
        <p className="h3" style={{ margin: 0 }}>Alerts on this phone</p>
        {status === "subscribed" ? (
          <span className="chip chip-sage">
            <span className="dot dot-live" style={{ background: "var(--color-sage)" }} />
            on
          </span>
        ) : null}
      </div>

      {!copy ? (
        <p className="small" style={{ margin: "10px 0 0", color: "var(--color-ink-3)" }}>
          Checking this browser…
        </p>
      ) : (
        <>
          <p className="h3" style={{ margin: "12px 0 0", fontSize: 14 }}>
            {copy.title}
          </p>
          <p className="small" style={{ margin: "4px 0 0" }}>
            {copy.body}
          </p>

          {error ? (
            <div style={{ marginTop: 10 }}>
              <Banner tone="red">{error}</Banner>
            </div>
          ) : null}

          {justOn ? (
            <div style={{ marginTop: 10 }}>
              <Banner tone="sage">
                Registered. Send yourself one from the control room to prove it arrives.
              </Banner>
            </div>
          ) : null}

          {proved ? (
            <div style={{ marginTop: 10 }}>
              <Banner tone="sage">
                Sent. If nothing appeared, the phone is blocking notifications for
                Aegis — check its settings before you rely on this.
              </Banner>
            </div>
          ) : null}

          <div style={{ display: "flex", gap: 8, marginTop: 12, flexWrap: "wrap" }}>
            {canAsk ? (
              <button type="button" className="btn btn-primary" onClick={on} disabled={busy}>
                {busy ? "Asking…" : copy.action}
              </button>
            ) : null}
            {status === "subscribed" ? (
              <>
                <button type="button" className="btn btn-quiet" onClick={prove} disabled={busy}>
                  {busy ? "Sending…" : "Send a test alert"}
                </button>
                <button type="button" className="btn btn-quiet" onClick={off} disabled={busy}>
                  Turn off on this device
                </button>
              </>
            ) : null}
            {status === "ios-install" ? (
              <button
                type="button"
                className="btn btn-quiet"
                onClick={() => setStatus("ios-install")}
                disabled
              >
                Waiting for install
              </button>
            ) : null}
          </div>

          {iosNeedsInstall() && status !== "ios-install" ? (
            <p className="micro" style={{ marginTop: 10 }}>
              On iPhone, alerts need the app on your Home Screen.
            </p>
          ) : null}
        </>
      )}
    </div>
  );
}
