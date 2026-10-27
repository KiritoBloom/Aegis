"use client";

import { useMemo, useState } from "react";
import { Banner, Empty, Fact, Skeleton } from "@/components/ui";
import {
  ago,
  bytes,
  clock,
  dayLabel,
  type LedgerRow,
  type LedgerTotals,
  type ConsentState,
  type PauseState,
} from "@/lib/api";
import { useApi } from "@/lib/useApi";

/**
 * "What left your home."
 *
 * The most important page in the product and the one most likely to be a lie in
 * someone else's build. Every row here is a real outbound request written by the
 * backend before the request was sent; the totals are SUMs at render time. If
 * the camera is idle the number is genuinely zero, and the page says so in the
 * largest type on the screen.
 *
 * The push-provider caveat is stated plainly. Aegis does not control what Google
 * or Apple does with a notification payload after it is handed over, and a
 * privacy page that omitted that would be selling a feeling.
 */
export default function PrivacyPage() {
  const ledger = useApi<{ items: LedgerRow[]; totals: LedgerTotals }>("/api/ledger", 20000);
  const privacy = useApi<{
    consent: ConsentState;
    pause: PauseState;
    retention: { local_days: number; upstream_hours: number };
    ledger: LedgerTotals;
  }>("/api/privacy", 20000);
  const today = useApi<{ date: string; count: number; events: { type: string; zone_id: string; ts: string; suppressed: number }[]; note: string }>(
    "/api/privacy/today",
    20000,
  );

  const [expanded, setExpanded] = useState<number | null>(null);
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const totals = ledger.data?.totals;
  const rows = ledger.data?.items ?? [];

  const todayBytes = useMemo(() => {
    const start = new Date();
    start.setHours(0, 0, 0, 0);
    return rows
      .filter((r) => new Date(r.created_at).getTime() >= start.getTime())
      .reduce((sum, r) => sum + (r.bytes || 0), 0);
  }, [rows]);

  const todayRows = useMemo(() => {
    const start = new Date();
    start.setHours(0, 0, 0, 0);
    return rows.filter((r) => new Date(r.created_at).getTime() >= start.getTime());
  }, [rows]);

  const paused = privacy.data?.pause.paused;

  const pause = async (on: boolean) => {
    setBusy(true);
    setMessage(null);
    try {
      await fetch(`/api/privacy/${on ? "pause" : "resume"}`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ by: "family", reason: "from the privacy page" }),
      });
      privacy.reload();
      setMessage(on ? "Monitoring paused. The camera stopped." : "Monitoring resumed.");
    } catch {
      setMessage("Could not change that. Try again.");
    } finally {
      setBusy(false);
    }
  };

  const deleteAll = async () => {
    if (confirm.trim().toUpperCase() !== "DELETE") return;
    setBusy(true);
    setMessage(null);
    try {
      const res = await fetch("/api/privacy/delete-all", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ confirm }),
      });
      if (!res.ok) throw new Error("failed");
      const body = (await res.json()) as {
        media?: { dirs_removed: number; files_removed: number; bytes: number };
        rows?: Record<string, number>;
        upstream?: string;
      };
      const rowCount = Object.values(body.rows ?? {}).reduce((a, b) => a + Math.max(0, b), 0);
      setMessage(
        `Deleted. ${body.media?.dirs_removed ?? 0} folders of footage (${bytes(body.media?.bytes ?? 0)}) and ${rowCount} rows of history are gone. ${body.upstream ?? ""}`,
      );
      setConfirm("");
      ledger.reload();
      privacy.reload();
      today.reload();
    } catch {
      setMessage("Could not delete. Nothing was removed.");
    } finally {
      setBusy(false);
    }
  };

  if (ledger.error) {
    return (
      <div style={{ maxWidth: 720 }}>
        <p className="eyebrow">Privacy</p>
        <h1 className="h1" style={{ margin: "8px 0 0" }}>The ledger is not reachable.</h1>
        <p className="lede" style={{ marginTop: 12 }}>
          This page cannot be shown from memory. If the ledger is unavailable, Aegis
          would rather show you nothing than a number it cannot prove.
        </p>
        <p className="mono micro" style={{ marginTop: 12 }}>{ledger.error}</p>
      </div>
    );
  }

  return (
    <div style={{ maxWidth: 940 }}>
      <header style={{ marginBottom: 22 }}>
        <p className="eyebrow">Privacy</p>
        <h1 className="h1" style={{ margin: "8px 0 0" }}>What left your home.</h1>
        <p className="lede" style={{ marginTop: 10, maxWidth: 600 }}>
          Every request Aegis makes to a model provider is written down here
          <em> before</em> it is sent. This page is not a summary of a log; it is
          the log.
        </p>
      </header>

      {paused ? (
        <div style={{ marginBottom: 16 }}>
          <Banner tone="sage">
            Monitoring is paused by {privacy.data?.pause.by || "the resident"}. The camera
            is not watching and nothing new is being stored.
          </Banner>
        </div>
      ) : null}

      {message ? (
        <div style={{ marginBottom: 16 }}>
          <Banner tone="sage">{message}</Banner>
        </div>
      ) : null}

      {/* the two numbers */}
      <section style={{ display: "grid", gap: 14, gridTemplateColumns: "1fr", marginBottom: 16 }} className="sm:grid-cols-2">
        <div className="card" style={{ padding: 22 }}>
          <p className="eyebrow" style={{ margin: 0 }}>While nothing was happening</p>
          {ledger.loading && !totals ? (
            <Skeleton h={64} w={180} r={10} />
          ) : (
            <p className="h-display" style={{ fontSize: "clamp(44px,8vw,68px)", margin: "4px 0 0" }}>
              {bytes(totals?.idle_bytes ?? 0)}
            </p>
          )}
          <p className="small" style={{ margin: "8px 0 0", maxWidth: 300 }}>
            uploaded while the house was quiet. A camera that streams would have sent
            gigabytes by now.
          </p>
        </div>

        <div className="card" style={{ padding: 22 }}>
          <p className="eyebrow" style={{ margin: 0 }}>Today</p>
          {ledger.loading && !totals ? (
            <Skeleton h={64} w={160} r={10} />
          ) : (
            <p className="h-display" style={{ fontSize: "clamp(44px,8vw,68px)", margin: "4px 0 0" }}>
              {bytes(todayBytes)}
            </p>
          )}
          <p className="small" style={{ margin: "8px 0 0", maxWidth: 320 }}>
            across {todayRows.length} request{todayRows.length === 1 ? "" : "s"}, for{" "}
            {totals?.video_bytes ? "video that needed a closer look" : "nothing yet"}.
            {totals?.audio_rows ? ` ${totals.audio_rows} of them carried her voice.` : ""}
          </p>
        </div>
      </section>

      {/* the honest caveat, not buried */}
      <div style={{ marginBottom: 22 }}>
        <Banner tone="amber">
          Notifications travel through Google or Apple push. Aegis controls what it
          sends and when; it does not control how long that provider keeps a
          notification. Clips are never attached to a push — only a short sentence and
          an id.
        </Banner>
      </div>

      {/* the table */}
      <section className="card" style={{ overflow: "hidden", marginBottom: 22 }}>
        <div style={{ padding: "16px 20px 10px", display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}>
          <p className="h3" style={{ margin: 0 }}>Every request</p>
          <span className="mono micro">{rows.length} rows · {bytes(totals?.total_bytes ?? 0)} total</span>
        </div>

        {ledger.loading && rows.length === 0 ? (
          <div style={{ padding: "0 20px 20px", display: "grid", gap: 8 }}>
            <Skeleton h={38} />
            <Skeleton h={38} />
          </div>
        ) : rows.length === 0 ? (
          <Empty title="Nothing has left the house.">
            When something happens, the request appears here with its size, its
            destination, and how long the provider keeps it.
          </Empty>
        ) : (
          <div>
            {rows.map((row, i) => {
              const open = expanded === row.id;
              return (
                <div key={row.id} style={{ borderTop: i === 0 ? "1px solid var(--color-line)" : "1px solid var(--color-line)" }}>
                  <button
                    onClick={() => setExpanded(open ? null : row.id)}
                    aria-expanded={open}
                    style={{
                      width: "100%",
                      display: "grid",
                      gridTemplateColumns: "1fr auto auto",
                      gap: 12,
                      alignItems: "center",
                      padding: "11px 20px",
                      background: "none",
                      border: "none",
                      textAlign: "left",
                      cursor: "pointer",
                    }}
                  >
                    <span style={{ minWidth: 0 }}>
                      <span className="h3" style={{ display: "block", fontSize: 13.5 }}>
                        {purposeLabel(row.purpose)}
                        {row.contains_audio ? <span className="chip chip-amber" style={{ marginLeft: 8 }}>voice</span> : null}
                        {row.contains_video ? <span className="chip" style={{ marginLeft: 8 }}>video</span> : null}
                      </span>
                      <span className="micro" style={{ display: "block", marginTop: 2 }}>
                        {dayLabel(row.created_at)} {clock(row.created_at)} · {row.destination}
                      </span>
                    </span>
                    <span className="mono" style={{ fontSize: 13 }}>{bytes(row.bytes)}</span>
                    <span className="mono micro" style={{ color: "var(--color-ink-3)", minWidth: 44, textAlign: "right" }}>
                      {row.retention}
                    </span>
                  </button>

                  {open ? (
                    <div style={{ padding: "0 20px 14px", background: "var(--color-paper-2)" }}>
                      <div style={{ paddingTop: 10 }}>
                        <Fact label="Exact endpoint" value={row.destination} />
                        <Fact label="Purpose" value={row.purpose} />
                        <Fact label="Model" value={row.model || "—"} />
                        <Fact label="Frames" value={row.frames || 0} />
                        <Fact label="Contains video" value={row.contains_video ? "yes" : "no"} />
                        <Fact label="Contains voice" value={row.contains_audio ? "yes" : "no"} />
                        <Fact label="Provider keeps it" value={row.retention} />
                        <Fact label="Took" value={row.latency_ms ? `${row.latency_ms} ms` : "—"} />
                        <Fact label="Incident" value={row.incident_id || "—"} />
                      </div>
                    </div>
                  ) : null}
                </div>
              );
            })}
          </div>
        )}
      </section>

      {/* today, in the resident's words */}
      <section className="card" style={{ padding: 20, marginBottom: 22 }}>
        <p className="h3" style={{ margin: 0 }}>What was noticed today</p>
        <p className="micro" style={{ margin: "4px 0 12px" }}>
          {today.data?.note ?? "Moments the system noticed. No footage is kept for these."}
        </p>
        {today.data && today.data.events.length > 0 ? (
          <div style={{ display: "grid", gap: 0 }}>
            {today.data.events.slice(0, 12).map((ev, i) => (
              <div
                key={i}
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  gap: 12,
                  padding: "7px 0",
                  borderTop: "1px solid var(--color-line)",
                }}
              >
                <span className="small" style={{ textTransform: "capitalize" }}>
                  {ev.type.replace(/_/g, " ")} · {ev.zone_id?.replace(/_/g, " ") ?? "—"}
                </span>
                <span className="mono micro" style={{ color: "var(--color-ink-3)" }}>
                  {clock(ev.ts)} {ev.suppressed ? "· quiet" : ""}
                </span>
              </div>
            ))}
          </div>
        ) : (
          <p className="small" style={{ margin: 0 }}>Nothing has been noticed today.</p>
        )}
      </section>

      {/* retention + control */}
      <section style={{ display: "grid", gap: 14, gridTemplateColumns: "1fr" }} className="sm:grid-cols-2">
        <div className="card" style={{ padding: 20 }}>
          <p className="h3" style={{ margin: 0 }}>How long anything is kept</p>
          <div style={{ marginTop: 8 }}>
            <Fact label="Footage on this machine" value={`${privacy.data?.retention.local_days ?? 7} days`} />
            <Fact label="Clip at the model provider" value={`${privacy.data?.retention.upstream_hours ?? 1} hour`} />
            <Fact label="Alert history" value="90 days" />
            <Fact label="Voice recordings" value="deleted after transcription" />
            <Fact label="Face recognition" value="never" />
            <Fact label="Training on her data" value="never" />
          </div>
          <p className="micro" style={{ margin: "10px 0 0" }}>
            Deletion runs by itself every hour. This is not a policy that depends on
            somebody remembering.
          </p>
        </div>

        <div className="card" style={{ padding: 20 }}>
          <p className="h3" style={{ margin: 0 }}>Control</p>
          <p className="small" style={{ margin: "4px 0 12px" }}>
            Pausing is instant and honest: the camera stops, and the family sees that
            it stopped rather than a quiet house.
          </p>
          <button
            className={paused ? "btn btn-primary" : "btn"}
            style={{ width: "100%", justifyContent: "center" }}
            onClick={() => pause(!paused)}
            disabled={busy}
          >
            {paused ? "Resume monitoring" : "Pause monitoring"}
          </button>

          <hr className="hairline" style={{ margin: "18px 0" }} />

          <p className="h3" style={{ margin: 0, color: "var(--color-red)" }}>Delete everything</p>
          <p className="small" style={{ margin: "4px 0 10px" }}>
            Removes all footage, all alerts, and all history from this machine. One
            audit row is kept so the family can see that it happened.
          </p>
          <label style={{ display: "grid", gap: 6 }}>
            <span className="micro">Type DELETE to confirm</span>
            <input
              className="input mono"
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              placeholder="DELETE"
              autoComplete="off"
              aria-label="Type DELETE to confirm"
            />
          </label>
          <button
            className="btn btn-danger"
            style={{ width: "100%", justifyContent: "center", marginTop: 10 }}
            onClick={deleteAll}
            disabled={busy || confirm.trim().toUpperCase() !== "DELETE"}
          >
            Delete everything
          </button>
        </div>
      </section>

      <p className="micro" style={{ marginTop: 20, maxWidth: 620 }}>
        Last ledger write {rows[0] ? ago(rows[0].created_at) : "never"}. Aegis is not a
        medical device and does not call an ambulance; it tells a person who can.
      </p>
    </div>
  );
}

function purposeLabel(purpose: string): string {
  switch (purpose) {
    case "sam_track":
      return "Finding the person in the clip";
    case "agent_decision":
      return "Deciding what to do about it";
    case "transcribe":
      return "Understanding her answer";
    case "digest":
      return "Writing the morning summary";
    case "letter":
      return "Writing the weekly letter";
    case "idle_probe":
      return "Idle check";
    default:
      return purpose.replace(/_/g, " ");
  }
}
