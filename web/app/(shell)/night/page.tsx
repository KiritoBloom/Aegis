"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { NightStrip, nightHeadline } from "@/components/night";
import { Icon } from "@/components/icons";
import { Banner, Fact, SeverityChip, Skeleton } from "@/components/ui";
import {
  clock,
  typeLabel,
  zoneLabel,
  type AegisEvent,
  type Household,
  type NightView,
  type VisitorState,
} from "@/lib/api";
import { useApi, useStream } from "@/lib/useApi";

/** Night record for 22:00–06:00, browsable by date. */
export default function NightPage() {
  const [offset, setOffset] = useState(0);
  const night = useApi<NightView>(`/api/night?offset=${offset}`, 20000);
  const household = useApi<Household>("/api/household", 0);
  const visitors = useApi<VisitorState>("/api/visitors/expected", 30000);
  const [visitorBusy, setVisitorBusy] = useState(false);
  const [visitorNote, setVisitorNote] = useState<string | null>(null);
  const [noteBusy, setNoteBusy] = useState(false);
  const [noteError, setNoteError] = useState<string | null>(null);

  useStream((frame) => {
    if (frame.startsWith("incident.") || frame === "snapshot" || frame === "context.read" || frame === "night.note") {
      night.reload();
    }
    if (frame === "visitor.expected") {
      visitors.reload();
      night.reload();
    }
  });

  const view = night.data;
  const headline = view
    ? nightHeadline(view.summary)
    : {
        title: night.loading ? "Reading the night…" : "Night watch",
        detail: "The last eight hours between 22:00 and 06:00.",
      };

  const dateLabel = useMemo(() => {
    if (!view) return "22:00 – 06:00";
    const start = new Date(view.start);
    const end = new Date(view.end);
    const startText = start.toLocaleDateString("en-GB", { day: "numeric", month: "long" });
    const endText = end.toLocaleDateString("en-GB", { day: "numeric", month: "long" });
    return `${startText}${startText === endText ? "" : ` – ${endText}`}`;
  }, [view]);

  const door = view?.events.filter((e) => String(e.type).startsWith("door")) ?? [];
  const presence = view?.events.filter((e) => e.type === "unexpected_presence") ?? [];
  const quiet = view?.events.filter((e) => e.suppressed) ?? [];
  const alerts = view?.incidents.filter((i) => i.severity === "urgent" || i.severity === "emergency") ?? [];
  const checkins = view?.events.filter((e) => e.type === "checkin_no_response") ?? [];
  const watch = household.data?.watch;

  const setVisitor = async (minutes: number | null) => {
    setVisitorBusy(true);
    setVisitorNote(null);
    try {
      const res = await fetch("/api/visitors/expected", {
        method: minutes === null ? "DELETE" : "POST",
        headers: { "content-type": "application/json" },
        body: minutes === null ? undefined : JSON.stringify({ minutes, by: "family" }),
      });
      if (!res.ok) throw new Error(`Could not update that (${res.status})`);
      setVisitorNote(minutes === null ? "Cleared. A second person after dark will alert again." : "Marked expected. A second person after dark will be recorded quietly.");
      visitors.reload();
      night.reload();
    } catch (err) {
      setVisitorNote(err instanceof Error ? err.message : "Could not update that.");
    } finally {
      setVisitorBusy(false);
    }
  };

  const writeNote = async () => {
    if (!view) return;
    setNoteBusy(true);
    setNoteError(null);
    try {
      const res = await fetch("/api/night/note", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ offset }),
      });
      if (!res.ok) throw new Error(`Could not write the note (${res.status})`);
      night.reload();
    } catch (err) {
      setNoteError(err instanceof Error ? err.message : "Could not write the note.");
    } finally {
      setNoteBusy(false);
    }
  };

  if (night.error) {
    return (
      <div style={{ maxWidth: 760 }}>
        <p className="eyebrow">Night watch</p>
        <h1 className="h1" style={{ margin: "8px 0 0" }}>The night is not readable.</h1>
        <p className="lede" style={{ marginTop: 12 }}>
          Aegis could not load the night record. It will not fill the gap with a
          reassuring sentence; the camera page still has its own heartbeat.
        </p>
        <p className="mono micro" style={{ marginTop: 12 }}>{night.error}</p>
        <button className="btn" style={{ marginTop: 16 }} onClick={night.reload}>Try again</button>
      </div>
    );
  }

  return (
    <div style={{ maxWidth: 1040, paddingBottom: 20 }}>
      <div className="reveal">
        <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: 16, flexWrap: "wrap" }}>
          <p className="eyebrow" style={{ margin: 0 }}>
            Night watch{offset === 0 ? (view?.active ? " · tonight so far" : " · last night") : " · earlier night"}
          </p>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span className="mono micro">{dateLabel}</span>
            <span style={{ width: 1, height: 12, background: "var(--color-line)" }} aria-hidden="true" />
            <button
              type="button"
              className="btn"
              onClick={() => setOffset((o) => Math.max(-31, o - 1))}
              disabled={offset <= -31}
              style={{ minHeight: 30, padding: "0 9px", fontSize: 11.5 }}
              aria-label="Earlier night"
            >
              ← Earlier
            </button>
            <button
              type="button"
              className="btn"
              onClick={() => setOffset((o) => Math.min(0, o + 1))}
              disabled={offset === 0}
              style={{ minHeight: 30, padding: "0 9px", fontSize: 11.5 }}
              aria-label="Later night"
            >
              Later →
            </button>
          </div>
        </div>

        <section className="night-hero" style={{ marginTop: 8 }}>
          <div>
            <p className="eyebrow" style={{ margin: 0, color: "var(--color-ink-3)" }}>
              22:00 – 06:00
            </p>
            <h1 className="night-hero-title">{headline.title}</h1>
            <p className="night-hero-detail">{headline.detail}</p>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 16 }}>
              <Link href="/live" className="btn btn-quiet" style={{ minHeight: 34, fontSize: 12.5, padding: "0 11px" }}>
                <Icon name="camera" size={15} /> Camera
              </Link>
              <Link href="/privacy" className="btn btn-quiet" style={{ minHeight: 34, fontSize: 12.5, padding: "0 11px" }}>
                <Icon name="shield" size={15} /> What left the house
              </Link>
            </div>
          </div>
          <div className="night-hero-side">
            {view ? (
              <>
                <Fact label="Door events" value={view.summary.door} />
                <Fact label="Second person" value={view.summary.unexpected} />
                <Fact label="Alerts" value={view.summary.alerts} />
                <Fact label="Stayed quiet" value={view.summary.quiet} />
              </>
            ) : (
              <div style={{ display: "grid", gap: 10 }}>
                <Skeleton h={14} />
                <Skeleton h={14} />
                <Skeleton h={14} />
                <Skeleton h={14} />
              </div>
            )}
          </div>
        </section>
      </div>

      {view ? (
        <section className="reveal" style={{ marginTop: 16, animationDelay: "60ms" }}>
          <div className="card" style={{ padding: 18 }}>
            <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: 12, marginBottom: 10 }}>
              <p className="h3" style={{ margin: 0 }}>The dark hours, in order</p>
              <span className="mono micro">{view.summary.events} recorded</span>
            </div>
            <NightStrip view={view} />
            <p className="micro" style={{ margin: "10px 0 0", maxWidth: 660 }}>
              Each dot is an event the camera reported. A hollow dot stayed quiet. Times are local;
              the strip starts at 22:00 and ends at 06:00.
            </p>
          </div>
        </section>
      ) : (
        <div style={{ marginTop: 16 }}><Skeleton h={150} r={14} /></div>
      )}

      {view ? (
        <section className="card night-note reveal" style={{ marginTop: 16, animationDelay: "90ms" }}>
          <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
            <p className="eyebrow" style={{ margin: 0 }}>
              {view.active ? "Tonight, in words" : "The morning note"}
            </p>
            {view.note ? (
              <span className="chip mono" style={{ textTransform: "none" }}>
                {view.note.model === "template" ? "template" : "Muse Spark"} · {clock(view.note.created_at)}
              </span>
            ) : null}
          </div>
          {view.note ? (
            <>
              <p className="night-note-body">{view.note.note}</p>
              {view.note.check ? (
                <p className="small" style={{ margin: "10px 0 0", color: "var(--color-ink-2)" }}>
                  <span className="eyebrow" style={{ marginRight: 7 }}>Worth checking</span>
                  {view.note.check}
                </p>
              ) : null}
            </>
          ) : (
            <>
              <p className="night-note-body" style={{ color: "var(--color-ink-2)" }}>
                A few calm sentences, written from the event rows. No pixels, no identities,
                no advice.
              </p>
              <button
                type="button"
                className="btn"
                style={{ marginTop: 12, minHeight: 36, fontSize: 12.5 }}
                onClick={() => void writeNote()}
                disabled={noteBusy || view.summary.events === 0}
              >
                {noteBusy ? "Muse is writing…" : view.active ? "Write tonight so far" : "Write the morning note"}
              </button>
              {view.summary.events === 0 ? (
                <p className="micro" style={{ margin: "8px 0 0" }}>There are no rows to write from.</p>
              ) : null}
            </>
          )}
          {noteError ? <p role="alert" className="micro" style={{ color: "var(--color-red)", margin: "8px 0 0" }}>{noteError}</p> : null}
        </section>
      ) : null}

      <div className="night-page-grid reveal" style={{ animationDelay: "120ms" }}>
        <div style={{ display: "grid", gap: 16 }}>
          <section className="card" style={{ padding: 20 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 9 }}>
              <Icon name="door" size={17} />
              <p className="night-card-title">The door</p>
            </div>
            <p className="small" style={{ margin: "7px 0 0", maxWidth: 560 }}>
              A door event is a time and a state, not an identity. The clip is where a family sees
              what happened.
            </p>
            <div style={{ marginTop: 10 }}>
              {door.length ? (
                door.slice(0, 6).map((ev, i) => (
                  <NightEventRow key={ev.id ?? `${ev.type}-${i}`} event={ev} />
                ))
              ) : (
                <p className="mono micro" style={{ margin: "12px 0 0", color: "var(--color-ink-3)" }}>
                  No door events were recorded in this window.
                </p>
              )}
            </div>
          </section>

          <section className="card" style={{ padding: 20 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 9 }}>
              <Icon name="night" size={17} />
              <p className="night-card-title">Who else was there</p>
            </div>
            <p className="small" style={{ margin: "7px 0 0", maxWidth: 560 }}>
              Aegis notices a second person after dark. It does not know whether that person is a
              caregiver, a neighbour, or a stranger.
            </p>
            <div style={{ marginTop: 10 }}>
              {presence.length ? (
                presence.slice(0, 6).map((ev, i) => (
                  <NightEventRow key={ev.id ?? `${ev.type}-${i}`} event={ev} />
                ))
              ) : (
                <p className="mono micro" style={{ margin: "12px 0 0", color: "var(--color-ink-3)" }}>
                  No second person was recorded in the watched areas.
                </p>
              )}
            </div>
          </section>

          <section className="card" style={{ padding: 20 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 9 }}>
              <Icon name="alerts" size={17} />
              <p className="night-card-title">What reached a phone</p>
            </div>
            <div style={{ marginTop: 10 }}>
              {alerts.length ? (
                alerts.slice(0, 6).map((inc) => (
                  <Link key={inc.id} href={`/alerts/${inc.id}`} className="row-link night-row" style={{ textDecoration: "none" }}>
                    <span className="mono micro">{clock(inc.local_time || inc.created_at)}</span>
                    <span className="sev-bar" style={{ background: "var(--color-red)" }} aria-hidden="true" />
                    <span style={{ minWidth: 0 }}>
                      <span className="h3" style={{ display: "block", fontSize: 13.5 }}>
                        {inc.title || typeLabel(inc.type)}
                      </span>
                      <span className="micro" style={{ display: "block" }}>
                        {zoneLabel(inc.zone_id)}
                        {inc.source === "sim" ? " · simulated" : ""}
                      </span>
                    </span>
                    <span className="night-row-meta">
                      <SeverityChip severity={inc.severity} />
                    </span>
                  </Link>
                ))
              ) : (
                <p className="mono micro" style={{ margin: "12px 0 0", color: "var(--color-ink-3)" }}>
                  No one was woken. The night page is not a claim that nothing happened.
                </p>
              )}
            </div>
          </section>
        </div>

        <div style={{ display: "grid", gap: 16, alignContent: "start" }}>
          <section className="card" style={{ padding: 20 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 9 }}>
              <Icon name="night" size={17} />
              <p className="night-card-title">Expected visitor</p>
            </div>
            {visitors.data?.expected ? (
              <>
                <p className="h3" style={{ margin: "8px 0 0" }}>
                  {visitors.data.label || "Someone is"} expected until {clock(visitors.data.until)}
                </p>
                <p className="small" style={{ margin: "6px 0 0" }}>
                  A second person after dark will still be recorded, but it will not wake
                  you. The door and the clip remain in the night record.
                </p>
                <button
                  type="button"
                  className="btn"
                  style={{ marginTop: 12, minHeight: 36, fontSize: 12.5 }}
                  onClick={() => void setVisitor(null)}
                  disabled={visitorBusy}
                >
                  Clear expected visitor
                </button>
              </>
            ) : (
              <>
                <p className="h3" style={{ margin: "8px 0 0" }}>Expecting someone tonight?</p>
                <p className="small" style={{ margin: "6px 0 0" }}>
                  Mark a caregiver or family visitor expected. The night-presence rule
                  stays quiet for that window instead of waking you.
                </p>
                <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 12 }}>
                  <button
                    type="button"
                    className="btn"
                    style={{ minHeight: 36, fontSize: 12.5 }}
                    onClick={() => void setVisitor(120)}
                    disabled={visitorBusy}
                  >
                    For 2 hours
                  </button>
                  <button
                    type="button"
                    className="btn btn-quiet"
                    style={{ minHeight: 36, fontSize: 12.5 }}
                    onClick={() => void setVisitor(minutesUntilMorning())}
                    disabled={visitorBusy}
                  >
                    Until morning
                  </button>
                </div>
              </>
            )}
            {visitorNote ? (
              <p className="micro" style={{ margin: "9px 0 0", color: "var(--color-ink-2)" }}>{visitorNote}</p>
            ) : null}
            <p className="micro" style={{ margin: "10px 0 0" }}>
              This is not a door unlock or a security system. It only changes how
              Aegis handles a second person after dark.
            </p>
          </section>

          <section className="card" style={{ padding: 20 }}>
            <p className="eyebrow" style={{ margin: 0 }}>The night plan</p>
            {watch ? (
              <div style={{ marginTop: 8 }}>
                <Fact label="Quiet hours" value={watch.quiet.enabled ? `${watch.quiet.start}:00 – ${watch.quiet.end}:00` : "off"} />
                <Fact label="Always alerting" value={watch.locked.length} />
                <Fact label="Held for morning" value={watch.digest.length} />
                <Fact label="Watched areas off" value={watch.zones_off?.length ?? 0} />
              </div>
            ) : (
              <Skeleton h={90} />
            )}
            <p className="micro" style={{ margin: "10px 0 0" }}>
              A fall, an unanswered check-in, the camera going blind, and a second person after dark
              cannot be muted. They are safety nets, not preferences.
            </p>
          </section>

          <section className="card-flat" style={{ padding: 18 }}>
            <p className="eyebrow" style={{ margin: 0 }}>Held back</p>
            {quiet.length ? (
              <div style={{ marginTop: 6 }}>
                {quiet.slice(0, 8).map((ev, i) => (
                  <div key={ev.id ?? `${ev.type}-${i}`} className="night-row" style={{ gridTemplateColumns: "46px 8px 1fr", padding: "8px 0" }}>
                    <span className="mono micro">{clock(ev.ts)}</span>
                    <span className="night-legend-dot" style={{ marginTop: 5 }} data-quiet="true" aria-hidden="true" />
                    <span>
                      <span style={{ display: "block", fontSize: 12.5 }}>{typeLabel(ev.type)}</span>
                      <span className="micro" style={{ display: "block" }}>{reasonLabel(ev.reason)}</span>
                    </span>
                  </div>
                ))}
              </div>
            ) : (
              <p className="small" style={{ margin: "6px 0 0" }}>
                Nothing was held back in this window.
              </p>
            )}
          </section>

          <section className="card-flat" style={{ padding: 18 }}>
            <p className="eyebrow" style={{ margin: 0 }}>A limit worth stating</p>
            <p className="small" style={{ margin: "6px 0 0" }}>
              Aegis does not identify faces, cannot see every room, and does not dispatch anyone.
              The night page shows what the camera recorded; it cannot promise that the dark was
              empty. If someone is in danger, call emergency services.
            </p>
          </section>
        </div>
      </div>

      {checkins.length ? (
        <div className="reveal" style={{ marginTop: 16, animationDelay: "160ms" }}>
          <Banner tone="amber">
            {checkins.length} check-in{checkins.length === 1 ? "" : "s"} went unanswered after dark.
            The related alerts are in <Link href="/events" className="mono" style={{ textDecoration: "underline" }}>Events</Link>.
          </Banner>
        </div>
      ) : null}

      <p className="micro reveal" style={{ margin: "18px 0 0", maxWidth: 620, animationDelay: "180ms" }}>
        Night window: 22:00–06:00, household time. The record is events, not a recording of a person.
      </p>
    </div>
  );
}

function NightEventRow({ event }: { event: AegisEvent }) {
  return (
    <div className="night-row">
      <span className="mono micro">{clock(event.ts)}</span>
      <span
        className="night-legend-dot"
        data-severity={event.severity}
        data-quiet={event.suppressed ? "true" : undefined}
        aria-hidden="true"
        style={{ marginTop: 5 }}
      />
      <span>
        <span style={{ display: "block", fontSize: 13 }}>{typeLabel(event.type)}</span>
        <span className="micro" style={{ display: "block" }}>
          {zoneLabel(event.zone_id)} · {event.suppressed ? reasonLabel(event.reason) : "reached a phone"}
        </span>
      </span>
      <span className="mono micro night-row-meta" style={{ color: "var(--color-ink-3)" }}>
        {event.suppressed ? "quiet" : event.severity}
      </span>
    </div>
  );
}

function reasonLabel(reason: string | null | undefined): string {
  if (!reason) return "not an alert";
  const r = reason.toLowerCase();
  if (r.includes("expected_visitor")) return "expected visitor";
  if (r.includes("daytime_visitor")) return "daylight visitor";
  if (r.includes("muted_by_household")) return "you asked not to be told";
  if (r.includes("held_for_digest")) return "held for the morning letter";
  if (r.includes("monitoring_paused")) return "monitoring was paused";
  if (r.includes("recurring_ritual")) return "a daily ritual, not a fall";
  if (r.includes("recovered")) return "she got up again";
  if (r.includes("second_person")) return "someone was with her";
  if (r.includes("approach_only")) return "someone at the door, nothing more";
  if (r.includes("person_in_kitchen")) return "she was still in the kitchen";
  return reason.split(",")[0].replace(/_/g, " ");
}

function minutesUntilMorning(): number {
  const now = new Date();
  const end = new Date(now);
  end.setHours(6, 0, 0, 0);
  if (end.getTime() <= now.getTime()) end.setDate(end.getDate() + 1);
  return Math.max(5, Math.min(720, Math.ceil((end.getTime() - now.getTime()) / 60000)));
}
