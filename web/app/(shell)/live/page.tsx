"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { Banner, CameraFrame, Fact, Icon, Skeleton } from "@/components/ui";
import {
  ago,
  clock,
  typeLabel,
  zoneLabel,
  type AegisEvent,
  type Camera,
  type Incident,
} from "@/lib/api";
import { useApi, useStream } from "@/lib/useApi";

/**
 * The camera.
 *
 * A CCTV page that shows a live video feed is easy and dishonest: it implies
 * the family can watch her all day, which is the exact thing this product is
 * built not to do. So this page shows the *last frame the system kept*, with the
 * geometry the rules actually use drawn on top of it, plus the state machine's
 * current opinion. It answers "is the camera alive and what is it thinking",
 * which is the question someone asks at 11 PM when the app has gone quiet.
 */
export default function LivePage() {
  const camera = useApi<Camera>("/api/camera", 6000);
  const events = useApi<{ items: AegisEvent[] }>("/api/events", 6000);
  const incidents = useApi<{ items: Incident[] }>("/api/incidents?limit=8", 15000);
  const [active, setActive] = useState<string | null>(null);
  const [showMasks, setShowMasks] = useState(true);

  useStream((frame) => {
    if (frame.startsWith("incident.") || frame === "snapshot") {
      camera.reload();
      events.reload();
      incidents.reload();
    }
  });

  // Keep the highlight on a zone only while the event is fresh.
  const newest = events.data?.items?.[0];
  useEffect(() => {
    if (!newest) return;
    setActive(newest.zone_id ?? null);
    const t = setTimeout(() => setActive(null), 12000);
    return () => clearTimeout(t);
  }, [newest?.id, newest?.zone_id]);

  const cam = camera.data?.camera;
  const zones = useMemo(() => camera.data?.zones ?? [], [camera.data]);
  const masks = camera.data?.masks ?? [];
  const drawn = showMasks ? zones : zones.filter((z) => !z.private);

  const lastEvent = newest;
  const lastIncident = incidents.data?.items?.[0];

  return (
    <div style={{ maxWidth: 1000 }}>
      <header style={{ marginBottom: 18 }}>
        <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: 14 }}>
          <p className="eyebrow">Camera</p>
          <span className="mono micro">{cam ? `${cam.id}` : "—"}</span>
        </div>
        <h1 className="h1" style={{ margin: "8px 0 0" }}>What the house can see.</h1>
        <p className="lede" style={{ marginTop: 10, maxWidth: 600 }}>
          One camera, pointing at the hall and the front door. The frame below is
          the last one Aegis kept — it is not a live feed, because watching her all
          day is the thing this system exists to avoid.
        </p>
      </header>

      {cam && !cam.online ? (
        <div style={{ marginBottom: 14 }}>
          <Banner tone="amber">
            No heartbeat since {ago(cam.last_seen)}. The camera may be unplugged or the
            network is down. Aegis cannot see right now, and it says so rather than
            pretending the house is quiet.
          </Banner>
        </div>
      ) : null}

      <div style={{ display: "grid", gap: 16, gridTemplateColumns: "1fr" }} className="lg:grid-cols-[1.5fr_1fr]">
        {/* the frame */}
        <section>
          {camera.loading && !camera.data ? (
            <Skeleton h={340} r={12} />
          ) : (
            <div style={{ borderRadius: 12, overflow: "hidden", border: "1px solid var(--color-line-2)" }}>
              <CameraFrame
                src={camera.data?.still?.url ?? null}
                zones={drawn}
                activeZone={active}
                caption={
                  <>
                    <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                      <span
                        className={cam?.online ? "dot dot-live" : "dot"}
                        style={{
                          width: 6,
                          height: 6,
                          borderRadius: 999,
                          background: cam?.online ? "#8fb89b" : "#c9822f",
                          display: "inline-block",
                        }}
                      />
                      {cam?.online ? "camera online" : "no signal"}
                    </span>
                    <span>
                      {camera.data?.still
                        ? `${clock(camera.data.still.at)} · ${typeLabel(camera.data.still.type)}`
                        : "no frame yet"}
                    </span>
                  </>
                }
              />
            </div>
          )}

          <div style={{ display: "flex", gap: 6, marginTop: 10, flexWrap: "wrap", alignItems: "center" }}>
            <button
              className="btn"
              aria-pressed={showMasks}
              onClick={() => setShowMasks((v) => !v)}
              style={{
                minHeight: 32,
                fontSize: 12.5,
                padding: "0 11px",
                background: showMasks ? "var(--color-ink)" : "transparent",
                color: showMasks ? "#fbf9f5" : "var(--color-ink-2)",
                borderColor: showMasks ? "var(--color-ink)" : "var(--color-line)",
              }}
            >
              <Icon name="shield" size={15} />
              {showMasks ? "Masks shown" : "Masks hidden"}
            </button>
            <span className="micro">
              {masks.length
                ? `${masks.join(", ").replace(/_/g, " ")} ${masks.length === 1 ? "is" : "are"} painted out before storage.`
                : "No private areas configured."}
            </span>
          </div>

          <div style={{ display: "flex", gap: 14, marginTop: 14, flexWrap: "wrap" }}>
            <Fact label="Detector" value={cam?.detector ?? "—"} />
            <Fact label="Frame rate" value={cam?.fps ? `${cam.fps} fps` : "—"} />
            <Fact label="Upload queue" value={cam?.queue_depth ?? 0} />
            <Fact label="Last heartbeat" value={ago(cam?.last_seen)} />
          </div>
        </section>

        {/* what it is thinking */}
        <section style={{ display: "grid", gap: 14, alignContent: "start" }}>
          <div className="card" style={{ padding: 18 }}>
            <p className="eyebrow" style={{ margin: 0 }}>Right now</p>
            <p className="h2" style={{ margin: "6px 0 0" }}>
              {lastEvent ? typeLabel(lastEvent.type) : "Nothing happening"}
            </p>
            <p className="micro" style={{ margin: "4px 0 0" }}>
              {lastEvent
                ? `${clock(lastEvent.ts)} · ${zoneLabel(lastEvent.zone_id)} · ${
                    lastEvent.suppressed ? "stayed quiet" : "alerted"
                  }`
                : "The last thing it noticed is old enough to stop mentioning."}
            </p>
            {lastIncident ? (
              <Link
                href={`/alerts/${lastIncident.id}`}
                className="btn btn-quiet"
                style={{ width: "100%", marginTop: 12, justifyContent: "space-between", minHeight: 36 }}
              >
                Open the last alert
                <Icon name="arrow" size={15} />
              </Link>
            ) : null}
          </div>

          <div className="card" style={{ padding: 18 }}>
            <p className="eyebrow" style={{ margin: 0 }}>Areas</p>
            <div style={{ marginTop: 8, display: "grid", gap: 2 }}>
              {zones.map((z) => (
                <div
                  key={z.name}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    gap: 10,
                    padding: "7px 0",
                    borderTop: "1px solid var(--color-line)",
                  }}
                >
                  <span style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0 }}>
                    <span
                      aria-hidden="true"
                      style={{
                        width: 7,
                        height: 7,
                        borderRadius: 999,
                        background: z.private
                          ? "#2a2f34"
                          : active === z.name
                            ? "var(--color-red)"
                            : "var(--color-line-2)",
                        flex: "none",
                      }}
                    />
                    <span className="h3" style={{ textTransform: "capitalize", fontSize: 13.5 }}>
                      {z.name.replace(/_/g, " ")}
                    </span>
                  </span>
                  <span className="mono micro" style={{ color: "var(--color-ink-3)" }}>
                    {z.private ? "masked" : z.type}
                  </span>
                </div>
              ))}
            </div>
          </div>

          <div className="card-flat" style={{ padding: 16 }}>
            <p className="eyebrow" style={{ margin: 0 }}>Why this is not a live feed</p>
            <p className="small" style={{ margin: "6px 0 0" }}>
              A stream would mean a camera pointed at a person all day, stored
              somewhere. Aegis keeps frames for seconds and a clip only when
              something happened. The frame above is the most recent one that
              survived that rule.
            </p>
            <Link href="/privacy" className="mono micro" style={{ display: "inline-block", marginTop: 8, textDecoration: "underline" }}>
              What is kept →
            </Link>
          </div>
        </section>
      </div>

      {/* the recent trail */}
      <section className="card" style={{ padding: 0, marginTop: 18, overflow: "hidden" }}>
        <p className="h3" style={{ margin: 0, padding: "16px 20px 10px" }}>What it has noticed</p>
        {(events.data?.items ?? []).slice(0, 8).map((ev, i) => (
          <div
            key={`${ev.id}-${i}`}
            style={{
              display: "grid",
              gridTemplateColumns: "3px 1fr auto",
              gap: 12,
              alignItems: "center",
              padding: "11px 20px",
              borderTop: "1px solid var(--color-line)",
            }}
          >
            <span
              className="sev-bar"
              style={{
                background: ev.suppressed
                  ? "var(--color-line-2)"
                  : ev.severity === "urgent" || ev.severity === "emergency"
                    ? "var(--color-red)"
                    : "var(--color-amber)",
              }}
              aria-hidden="true"
            />
            <span>
              <span className="h3" style={{ display: "block", fontSize: 13.5 }}>{typeLabel(ev.type)}</span>
              <span className="micro">{clock(ev.ts)} · {zoneLabel(ev.zone_id)}</span>
            </span>
            <span className="mono micro" style={{ color: "var(--color-ink-3)" }}>
              {ev.suppressed ? "quiet" : "alert"}
            </span>
          </div>
        ))}
        {(events.data?.items ?? []).length === 0 ? (
          <p className="small" style={{ padding: "0 20px 18px" }}>
            Nothing yet. The camera has not reported an event.
          </p>
        ) : null}
      </section>
    </div>
  );
}
