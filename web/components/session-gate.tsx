"use client";

import { useEffect, useState, type FormEvent } from "react";

type State = "checking" | "open" | "locked";

/** Household PIN gate. When no PIN is configured the app opens immediately. */
export function SessionGate({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<State>("checking");
  const [pin, setPin] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let alive = true;
    // A resident kiosk arrives with a short-lived signed check-in link. The
    // page's API validates that token; it must not be blocked by the family PIN.
    if (window.location.pathname.startsWith("/checkin/")
        && new URLSearchParams(window.location.search).has("t")) {
      setState("open");
      return () => { alive = false; };
    }
    fetch("/api/session", { cache: "no-store" })
      .then((res) => res.json())
      .then((data: { auth_required?: boolean; signed_in?: boolean }) => {
        if (alive) setState(data.auth_required && !data.signed_in ? "locked" : "open");
      })
      .catch(() => {
        // The backend being down should show the app's own error, not a lock
        // screen that cannot possibly open.
        if (alive) setState("open");
      });
    return () => {
      alive = false;
    };
  }, []);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const res = await fetch("/api/session", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ pin }),
      });
      if (!res.ok) {
        throw new Error(res.status === 429
          ? "Too many attempts. Wait a minute and try again."
          : "That PIN was not accepted.");
      }
      setState("open");
    } catch (err) {
      setError(err instanceof Error ? err.message : "That PIN was not accepted.");
    } finally {
      setBusy(false);
    }
  };

  if (state === "checking") {
    return (
      <div style={{ minHeight: "100dvh", display: "grid", placeItems: "center" }}>
        <p style={{ color: "var(--color-ink-3)", fontFamily: "var(--font-mono)" }}>
          Checking this device…
        </p>
      </div>
    );
  }

  if (state === "locked") {
    return (
      <div style={{ minHeight: "100dvh", display: "grid", placeItems: "center", padding: 24 }}>
        <main style={{
          width: "100%", maxWidth: 400, padding: 32,
          background: "var(--color-surface, #fff)",
          border: "1px solid var(--color-line, #e3ddd2)",
          borderRadius: 16,
          boxShadow: "0 18px 50px rgba(20, 24, 28, 0.07)",
        }}>
          <p style={{ margin: 0, fontFamily: "var(--font-mono)", fontSize: 11,
                      letterSpacing: "0.12em", textTransform: "uppercase",
                      color: "var(--color-ink-3, #79838b)" }}>
            Aegis · this household
          </p>
          <h1 style={{ margin: "10px 0 0", fontFamily: "var(--font-serif)",
                       fontSize: 34, lineHeight: 1.08, fontWeight: 500,
                       color: "var(--color-ink, #14181c)" }}>
            Enter the household PIN.
          </h1>
          <p style={{ margin: "10px 0 22px", fontSize: 14, lineHeight: 1.55,
                      color: "var(--color-ink-2, #4a5259)" }}>
            Alerts and clips are private to the people who live here. The PIN opens this
            browser for seven days.
          </p>
          <form onSubmit={submit} style={{ display: "grid", gap: 10 }}>
            <label htmlFor="household-pin" style={{ fontFamily: "var(--font-mono)",
                                                    fontSize: 11, letterSpacing: "0.08em",
                                                    color: "var(--color-ink-2, #4a5259)" }}>
              PIN
            </label>
            <input
              id="household-pin"
              type="password"
              inputMode="numeric"
              autoComplete="current-password"
              value={pin}
              onChange={(event) => setPin(event.target.value)}
              autoFocus
              style={{
                height: 50, width: "100%", padding: "0 14px", fontSize: 18,
                letterSpacing: "0.2em", color: "var(--color-ink, #14181c)",
                background: "var(--color-paper-2, #fbf9f5)",
                border: "1px solid var(--color-line-2, #d3cbbc)",
                borderRadius: 10, outline: "none",
              }}
            />
            {error ? (
              <p role="alert" style={{ margin: 0, fontSize: 13,
                                       color: "var(--color-red, #a93b32)" }}>{error}</p>
            ) : null}
            <button
              type="submit"
              disabled={busy || !pin}
              style={{
                height: 50, border: "none", borderRadius: 10, cursor: "pointer",
                background: "var(--color-ink, #14181c)",
                color: "var(--color-paper, #f6f3ed)",
                fontSize: 15, fontWeight: 600,
                opacity: busy || !pin ? 0.45 : 1,
                marginTop: 4,
              }}
            >
              {busy ? "Opening…" : "Open this household"}
            </button>
          </form>
          <p style={{ margin: "18px 0 0", fontFamily: "var(--font-mono)", fontSize: 11,
                      lineHeight: 1.5, color: "var(--color-ink-3, #79838b)" }}>
            No PIN? Leave <code>HOUSEHOLD_PIN</code> empty for a local demo.
          </p>
        </main>
      </div>
    );
  }

  return <>{children}</>;
}
