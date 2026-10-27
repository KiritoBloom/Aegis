"use client";

import Link from "next/link";
import { useMemo } from "react";
import {
  Banner,
  CameraFrame,
  Empty,
  Fact,
  FloorPlan,
  Icon,
  SeverityChip,
  Skeleton,
} from "@/components/ui";
import {
  ago,
  bytes,
  clock,
  dayLabel,
  typeLabel,
  zoneLabel,
  type AegisEvent,
  type Camera,
  type Household,
  type Incident,
  type Stats,
} from "@/lib/api";
import { useApi, useNow, useStream } from "@/lib/useApi";

/**
 * Today.
 *
 * The page is built around one number that is not an alert count: how much of
 * the day the system decided *not* to bother anybody about. Everything else on
 * the screen exists to make that number credible — the camera it came from, the
 * events it counted, the bytes that left the house to make it.
 */
export default function TodayPage() {
  const stats = useApi<Stats>("/api/stats", 20000);
  const incidents = useApi<{ items: Incident[] }>("/api/incidents?limit=6", 20000);
  const household = useApi<Household>("/api/household", 0);
  const camera = useApi<Camera>("/api/camera", 15000);
  const now = useNow(20000);

  useStream((frame) => {
    if (frame.startsWith("incident.") || frame === "snapshot") {
      stats.reload();
      incidents.reload();
      camera.reload();
    }
  });

  const restraint = stats.data?.restraint;
  const hb = (stats.data?.heartbeat ?? {}) as Record<string, unknown>;
  const paused = household.data?.privacy.pause.paused;
  const offline = camera.data ? !camera.data.camera.online : false;

  const recent = incidents.data?.items ?? [];
  const last = recent[0] ?? null;
  const stillUrl = camera.data?.still?.url ?? null;

  // The one-line prose under the big number. Derived, never written by hand.
  const prose = useMemo(() => {
    if (!restraint) return "";
    const days = restraint.window_days;
    const n = restraint.stayed_home;
    if (!n) return `Nothing needed you in the last ${days} days.`;
    const top = [...restraint.by_type].sort((a, b) => b.count - a.count).slice(0, 3);
    const names = top.map((t) => `${t.count} ${typeLabel(t.name).toLowerCase()}`);
    return `In the last ${days} days the house did ${n} things worth noticing. You heard about ${restraint.alerts} of them.`;
  }, [restraint]);

  if (stats.error) {
    return (
      <div style={{ maxWidth: 700 }}>
        <p className="eyebrow">Today</p>
        <h1 className="h1" style={{ margin: "8px 0 0" }}>The house is not answering.</h1>
        <p className="lede" style={{ marginTop: 12 }}>
          Aegis could not reach the machine running the camera. Nothing has been
          changed and no alert has been lost — the backend keeps them until it can
          tell you.
        </p>
        <p className="mono micro" style={{ marginTop: 14 }}>{stats.error}</p>
        <button className="btn" style={{ marginTop: 18 }} onClick={stats.reload}>
          Try again
        </button>
      </div>
    );
  }

  return (
    <div style={{ maxWidth: 980 }}>
      {/* ---------------------------------------------------------- header */}
      <header style={{ marginBottom: 22 }}>
        <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: 16 }}>
          <p className="eyebrow">Today</p>
          <span className="mono micro">
            {new Date(now).toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long" })}
          </span>
        </div>
        <h1 className="h1" style={{ margin: "8px 0 0" }}>
          {household.data ? household.data.profile.display_name : "The house"}
          &apos;s home.
        </h1>
      </header>

      {/* ------------------------------------------------- states §12.7 */}
      <div style={{ display: "grid", gap: 10, marginBottom: 18 }}>
        {paused ? (
          <Banner tone="sage">
            Monitoring paused by {household.data?.privacy.pause.by || "the resident"}
            {household.data?.privacy.pause.at ? ` at ${clock(household.data.privacy.pause.at)}` : ""}.
            The camera is not watching and nothing is being stored.
            <Link href="/profile" className="mono" style={{ marginLeft: 10, textDecoration: "underline" }}>
              Resume
            </Link>
          </Banner>
        ) : null}
        {offline ? (
          <Banner tone="amber">
            The camera has not sent a heartbeat since {ago(camera.data?.camera.last_seen)}. Aegis is
            blind right now, and this is the one failure it cannot hide from you.
            <Link href="/live" className="mono" style={{ marginLeft: 10, textDecoration: "underline" }}>
              Check camera
            </Link>
          </Banner>
        ) : null}
        {household.data && !household.data.onboarded ? (
          <Banner tone="sage" action={
            <Link href="/onboarding" className="btn btn-primary" style={{ minHeight: 36, padding: "0 14px" }}>
              Set it up
            </Link>
          }>
            You are using Aegis&apos;s default plan. Two minutes of setup decides what
            is worth waking you for.
          </Banner>
        ) : null}
        {(stats.data?.failed_notifications ?? 0) > 0 ? (
          <Banner tone="red">
            {stats.data?.failed_notifications} alert
            {stats.data?.failed_notifications === 1 ? "" : "s"} could not be delivered to a phone.
            They are still on the Alerts page.
          </Banner>
        ) : null}
      </div>

      {/* --------------------------------------------------- the counter */}
      <section className="card" style={{ padding: "26px 26px 20px", marginBottom: 16 }}>
        {stats.loading && !restraint ? (
          <div style={{ display: "grid", gap: 14 }}>
            <Skeleton h={12} w={140} />
            <Skeleton h={96} w={280} r={10} />
            <Skeleton h={16} w="70%" />
          </div>
        ) : (
          <>
            <p className="eyebrow" style={{ margin: 0 }}>
              Quiet by choice · last {restraint?.window_days ?? 7} days
            </p>
            <div style={{ display: "flex", alignItems: "flex-end", gap: 20, flexWrap: "wrap", marginTop: 4 }}>
              <span className="h-display" style={{ margin: 0 }}>
                {restraint?.stayed_home ?? 0}
              </span>
              <p className="lede" style={{ maxWidth: 400, margin: "0 0 14px" }}>
                things the house did that you were <em>not</em> told about.
              </p>
            </div>
            <p className="small" style={{ margin: "10px 0 0", maxWidth: 620 }}>
              {prose}
            </p>

            <div
              style={{
                display: "flex",
                flexWrap: "wrap",
                gap: "6px 18px",
                marginTop: 18,
                paddingTop: 14,
                borderTop: "1px solid var(--color-line)",
              }}
            >
              <Breakdown
                parts={[
                  `${restraint?.alerts ?? 0} alerted`,
                  ...(restraint?.by_type ?? [])
                    .filter((t) => ["door_open", "door_approach", "stove_unattended", "person_unwell_still"].includes(t.name))
                    .slice(0, 3)
                    .map((t) => `${t.count} ${typeLabel(t.name).toLowerCase()}`),
                ]}
              />
            </div>
            <p className="mono micro" style={{ margin: "12px 0 0", color: "var(--color-ink-3)" }}>
              {bytes(stats.data?.ledger?.idle_bytes ?? 0)} uploaded while idle ·{" "}
              {bytes(stats.data?.ledger?.video_bytes ?? 0)} of video for{" "}
              {restraint?.alerts ?? 0} alerts
            </p>
          </>
        )}
      </section>

      {/* ---------------------------------------------- plan + camera row */}
      <div style={{ display: "grid", gap: 16, gridTemplateColumns: "1fr", marginBottom: 16 }} className="lg:grid-cols-[1.35fr_1fr]">
        <section className="card" style={{ padding: 20 }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
            <p className="h3" style={{ margin: 0 }}>What is being watched</p>
            <Link href="/live" className="mono micro" style={{ textDecoration: "underline" }}>
              Camera
            </Link>
          </div>
          {camera.loading && !camera.data ? (
            <Skeleton h={220} r={10} />
          ) : camera.data ? (
            <>
              <div style={{ marginTop: 14, borderRadius: 10, overflow: "hidden", border: "1px solid var(--color-line)" }}>
                <CameraFrame
                  src={stillUrl}
                  zones={camera.data.zones}
                  activeZone={last?.zone_id ?? null}
                  caption={
                    <>
                      <span>{stillUrl ? `${camera.data.camera.id} · ${ago(camera.data.still?.at)}` : "no frame yet"}</span>
                      <span>{camera.data.camera.detector ?? "—"} · {camera.data.camera.fps ? `${camera.data.camera.fps} fps` : "—"}</span>
                    </>
                  }
                />
              </div>
              <p className="micro" style={{ margin: "10px 0 0" }}>
                The last frame the camera kept. The areas drawn on it are the ones the
                rules use; the solid black shapes are never stored at all.
              </p>
            </>
          ) : null}
        </section>

        <section className="card" style={{ padding: 20 }}>
          <p className="h3" style={{ margin: 0 }}>How this house is set up</p>
          {household.data ? (
            <>
              <div style={{ marginTop: 10 }}>
                <FloorPlan
                  zones={household.data.zones}
                  activeZone={last?.zone_id ?? null}
                  height={190}
                />
              </div>
              <div style={{ marginTop: 8 }}>
                <Fact label="Interrupting you" value={household.data.watch.alert.length + household.data.watch.locked.length} />
                <Fact label="Held for later" value={household.data.watch.digest.length} />
                <Fact label="Never shown" value={household.data.watch.off.length} />
                <Fact
                  label="Quiet hours"
                  value={
                    household.data.watch.quiet.enabled
                      ? `${household.data.watch.quiet.start}:00 – ${household.data.watch.quiet.end}:00`
                      : "off"
                  }
                />
              </div>
              <Link
                href="/onboarding"
                className="btn btn-quiet"
                style={{ width: "100%", marginTop: 10, justifyContent: "space-between" }}
              >
                {household.data.onboarded ? "Change what alerts me" : "Set up the plan"}
                <Icon name="arrow" size={16} />
              </Link>
            </>
          ) : (
            <Skeleton h={190} />
          )}
        </section>
      </div>

      {/* ------------------------------------------------------- recent */}
      <section className="card" style={{ padding: 0, overflow: "hidden" }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "16px 20px 10px" }}>
          <p className="h3" style={{ margin: 0 }}>Recent</p>
          <Link href="/alerts" className="mono micro" style={{ textDecoration: "underline" }}>
            All alerts
          </Link>
        </div>
        {incidents.loading && !incidents.data ? (
          <div style={{ padding: "0 20px 20px", display: "grid", gap: 10 }}>
            <Skeleton h={46} />
            <Skeleton h={46} />
          </div>
        ) : recent.length === 0 ? (
          <Empty title="No alerts. The house is quiet.">
            {hb.last_heartbeat_at || hb.ts ? (
              <>The camera last checked in {ago(String(hb.ts ?? hb.last_heartbeat_at))}.</>
            ) : (
              <>The camera has not checked in yet.</>
            )}
          </Empty>
        ) : (
          <div>
            {recent.map((inc) => (
              <Link
                key={inc.id}
                href={`/alerts/${inc.id}`}
                className="row-link"
                style={{
                  display: "grid",
                  gridTemplateColumns: "3px 1fr auto",
                  gap: 14,
                  alignItems: "center",
                  padding: "13px 20px",
                  borderTop: "1px solid var(--color-line)",
                }}
              >
                <span
                  className="sev-bar"
                  style={{
                    background:
                      inc.severity === "emergency" || inc.severity === "urgent"
                        ? "var(--color-red)"
                        : inc.severity === "watch"
                          ? "var(--color-amber)"
                          : "var(--color-sage)",
                  }}
                  aria-hidden="true"
                />
                <span style={{ minWidth: 0 }}>
                  <span className="h3" style={{ display: "block" }}>
                    {inc.title || typeLabel(inc.type)}
                  </span>
                  <span className="micro" style={{ display: "block", marginTop: 2 }}>
                    {zoneLabel(inc.zone_id)} · {dayLabel(inc.local_time || inc.created_at)}{" "}
                    {clock(inc.local_time || inc.created_at)}
                    {inc.source === "sim" ? <span className="chip chip-sim" style={{ marginLeft: 8 }}>simulated</span> : null}
                  </span>
                </span>
                <SeverityChip severity={inc.severity} />
              </Link>
            ))}
          </div>
        )}
      </section>

      {/* --------------------------------------------- the honest footer */}
      <section
        className="card-flat"
        style={{ padding: "14px 18px", marginTop: 16, display: "flex", gap: 14, flexWrap: "wrap", alignItems: "center", justifyContent: "space-between" }}
      >
        <span className="small" style={{ maxWidth: 560 }}>
          Aegis is not a medical device and does not call an ambulance. It tells a
          person who can.
        </span>
        <Link href="/privacy" className="mono micro" style={{ textDecoration: "underline" }}>
          What left the house →
        </Link>
      </section>
    </div>
  );
}

function Breakdown({ parts }: { parts: string[] }) {
  return (
    <span className="mono micro" style={{ color: "var(--color-ink-2)" }}>
      {parts.filter(Boolean).join(" · ")}
    </span>
  );
}
