"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { Icon } from "@/components/icons";
import { Modal } from "@/components/modal";
import { LiveFeed } from "@/components/live-feed";
import { FeedContext } from "@/components/feed-context";
import { useConsole } from "@/components/console-provider";
import { useApi, useNow, useStream } from "@/lib/useApi";
import { clock, typeLabel, type AegisEvent } from "@/lib/api";
import type { FeedSource, LogEntry, PipelineStep, WallFrame } from "@/lib/console-types";
import type { ConsoleFeed } from "@/lib/console";
import shared from "@/components/console-shared.module.css";
import styles from "@/components/console-home.module.css";

function timeOf(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleTimeString("en-GB", { hour12: false });
}

function feedState(active: boolean, error: string | undefined, frame: WallFrame | null | undefined,
                   liveStatus?: "idle" | "watching" | "ended" | "error") {
  if (!active) return { tone: "", label: "Standby", urgent: false, alert: false };
  if (error) return { tone: "error", label: "Link lost", urgent: false, alert: false };
  // What happened to the room outranks what happened to the recording: a feed
  // whose clip ended while verification was still running must keep showing the
  // verdict instead of freezing on "Recording ended".
  const status = (frame?.alert ?? frame?.last_alert)?.verification?.status;
  if (status === "notified") return { tone: "urgent", label: "Alert raised", urgent: true, alert: true };
  if (status === "verified") return { tone: "alert", label: "Resident check-in", urgent: false, alert: true };
  if (status === "unavailable") return { tone: "alert", label: "Local safety check", urgent: false, alert: true };
  if (status === "rejected") return { tone: "", label: "Not corroborated", urgent: false, alert: false };
  if (status === "recovered") return { tone: "", label: "Recovered", urgent: false, alert: false };
  if (status === "acknowledged") return { tone: "", label: "Family responding", urgent: false, alert: false };
  if (status === "verifying" || status === "pending") return { tone: "alert", label: "Verifying", urgent: false, alert: true };
  if (frame?.alert?.active) return { tone: "alert", label: "Local alert", urgent: false, alert: true };
  if (liveStatus === "ended") return { tone: "", label: "Recording ended", urgent: false, alert: false };
  if (frame) return { tone: "live", label: "Tracking", urgent: false, alert: false };
  return { tone: "", label: "Opening", urgent: false, alert: false };
}

export default function ConsoleHome() {
  const { setup, resident, active, switching, setActive, feeds, sources, live, logs, pipeline, pipelineError } = useConsole();
  const activity = useApi<{ items: AegisEvent[] }>("/api/events?limit=4", 15000);
  useStream((frame) => { if (frame.startsWith("incident.") || frame.startsWith("checkin.") || frame === "snapshot") activity.reload(); });
  const [focusId, setFocusId] = useState<string | null>(null);
  const now = useNow(1000);

  const residentName = resident?.name?.trim() || "Resident";
  const focused = feeds.find((feed) => feed.id === focusId) ?? null;

  useEffect(() => {
    if (!active) setFocusId(null);
  }, [active]);

  useEffect(() => {
    const requested = new URLSearchParams(window.location.search).get("feed");
    if (active && requested && feeds.some((feed) => feed.id === requested)) setFocusId(requested);
  }, [active, feeds]);

  const frame = focused ? live[focused.id]?.frame ?? null : null;
  const source = focused ? sources[focused.id] : undefined;
  const focusedLogs = focused ? (logs[focused.id] ?? []).filter((entry) => !entry.pipelineId).slice(-30) : [];
  const focusedSteps = focused ? pipeline.filter((step) => step.feed_id === focused.id) : [];

  const alertCount = useMemo(
    () => feeds.filter((feed) => live[feed.id]?.frame?.alert?.active).length,
    [feeds, live],
  );

  if (!setup) return null;

  return (
    <div className={styles.home}>
      <div className={shared.pageHead}>
        <div><p className={shared.kicker}>Your household, at a glance</p><h1 className={shared.pageTitle}>Home overview</h1>
          <p className={shared.pageLede}>{active ? "Your cameras, together. Expand a feed to follow its activity and event decisions." : `A private view of ${residentName}'s home. Start monitoring when you are ready.`}</p></div>
        <Link className={shared.smallButton} href="/settings">Manage cameras</Link>
      </div>
      <div className={styles.homeBar}>
        <div className={styles.homeNote}>
          <span className={styles.ink}>{active ? "LOCAL ANALYSIS RUNNING" : "ANALYSIS STOPPED"}</span>
          <span>CONTINUOUS VIEW</span>
          <span>{feeds.length} CAMERAS</span>
          {alertCount ? <span style={{ color: "var(--c-amber)" }}>{alertCount} ACTIVE ALERT{alertCount === 1 ? "" : "S"}</span> : null}
        </div>
        <div className={styles.homeRight}>
          <span className={shared.homeStat}>
            {residentName.toUpperCase()} · {setup.mapping ? Object.keys(setup.mapping).length : 0}/{feeds.length} MAPPED
          </span>
        </div>
      </div>

      {!active ? (
        <div className={shared.noteBar} style={{ marginBottom: 16 }}>
          <span>
            Monitoring is paused on this device. Start it when you are ready; leave this tab open for camera capture.
          </span>
          <button type="button" className={shared.primaryButton} disabled={switching} onClick={() => void setActive(true)}>
            {switching ? "Starting…" : "Start monitoring"}
          </button>
        </div>
      ) : null}

      {feeds.length ? (
        <div className={styles.wall} data-count={Math.min(feeds.length, 6)}>
          {feeds.map((feed, index) => {
            const state = live[feed.id];
            const data = state?.frame;
            const feedSource = sources[feed.id];
            const mapped = Boolean(setup.mapping[feed.id]);
            const error = feedSource?.error || state?.error;
            const receivedAt = state?.receivedAt;
            const stalled = active && receivedAt != null
              && state?.status !== "ended" && now - receivedAt > 12000 && !error;
            const status = active && !mapped
              ? { tone: "", label: "Mapping required", urgent: false, alert: false }
              : stalled
                ? { tone: "alert", label: "Analysis stalled", urgent: false, alert: true }
                : feedState(active, error, data, state?.status);
            const person = data?.persons?.find((item) => item.primary) ?? data?.persons?.[0];
            return (
              <button
                key={feed.id}
                type="button"
                className={styles.tile}
                data-alert={status.alert}
                data-urgent={status.urgent}
                onClick={() => { if (active && mapped) setFocusId(feed.id); }}
                disabled={!active || !mapped}
                aria-label={`Focus ${feed.name || feed.label}`}
              >
                {!active ? (
                  <span className={styles.tileStandby}>
                    <span className={styles.tileStandbyMark} />
                    <p>
                      <strong>{feed.name || feed.label}</strong>
                      Feed closed while the system is off
                    </p>
                  </span>
                ) : !mapped ? (
                  <span className={styles.tileStandby}>
                    <span className={styles.tileStandbyMark} />
                    <p>
                      <strong>Mapping required</strong>
                      Map this view in setup before it can be analyzed
                    </p>
                  </span>
                ) : error ? (
                  <span className={styles.tileError}>
                    <p>{error}</p>
                    <p className={styles.mono}>{feed.name || feed.label} · {feed.kind}</p>
                  </span>
                ) : !feedSource ? (
                  <span className={styles.tileLoading} />
                ) : (
                  <LiveFeed feed={feed} source={feedSource} frame={data} active={active} detailed={feeds.length <= 2} />
                )}
                {stalled ? (
                  <span className={styles.tileError}>
                    <p>Analysis has stopped returning frames for this feed.</p>
                    <p className={styles.mono}>Showing the last received image while the connection recovers.</p>
                  </span>
                ) : null}

                <span className={styles.tileChrome}>
                  <span className={styles.tileIdent}>
                    <span className={styles.tileCam}>CAM {String(index + 1).padStart(2, "0")}</span>
                    <span className={styles.tileName}>{feed.name || feed.label}</span>
                  </span>
                  <span className={styles.tileLive} data-off={!active || Boolean(error)}>
                    <span className={styles.liveDot} />
                    {!active ? "CLOSED" : error ? "NO SIGNAL" : live[feed.id]?.status === "ended" ? "FINAL FRAME" : !data?.tracking.fps ? "CONNECTING" : "LIVE VIEW"}
                  </span>
                </span>

                <span className={styles.tileExpand} aria-hidden="true">
                  <Icon name="expand" size={13} />
                </span>

                <span className={styles.tileBottom}>
                  <span className={styles.telemetry} data-alert={status.alert} data-urgent={status.urgent}>
                    <span><b>{data?.person_count ?? 0}</b> {(data?.person_count ?? 0) === 1 ? "PERSON" : "PEOPLE"}</span>
                    <span>{person?.posture === "sitting" ? "SEATED" : person?.posture === "unknown" ? "POSITION UNCLEAR" : person?.posture ? person.posture.toUpperCase() : "WAITING FOR A PERSON"}</span>
                    {data?.tracking?.lying_for_s ? <span>DOWN <b>{Math.round(data.tracking.lying_for_s)}S</b></span> : null}
                    {feed.location ? <span>{feed.location}</span> : null}
                  </span>
                  <span className={styles.tileState} data-tone={status.tone}>{status.label}</span>
                </span>
              </button>
            );
          })}
        </div>
      ) : (
        <div className={shared.page}>
          <div className={shared.emptyState}>
            <span className={shared.emptyStateMark}><Icon name="camera" size={17} /></span>
            <h2>No cameras in this console.</h2>
            <p>Add a recording, a device camera, or lab footage. Aegis cannot watch a room it has never been given.</p>
            <div>
              <Link className={shared.smallButton} href="/setup?step=cameras">Add a camera</Link>
            </div>
          </div>
        </div>
      )}

      <section className={styles.recentActivity}>
        <div className={styles.sectionHead}><h2>Recent activity</h2><Link href="/events?view=activity">View activity log <span aria-hidden="true">→</span></Link></div>
        {activity.error ? <p>Activity could not load. <button className={shared.ghostButton} onClick={activity.reload}>Retry</button></p>
          : activity.loading && !activity.data ? <p>Loading activity…</p>
          : activity.data?.items.length ? activity.data.items.map((event) => <div className={styles.recentRow} key={event.id}>
            <span>{clock(event.ts)}</span><strong>{typeLabel(event.type)}</strong><span className={styles.recentOutcome}>{event.suppressed ? "Handled quietly" : "Recorded"}</span>
          </div>) : <div className={styles.recentEmpty}><Icon name="shield" size={20} /><p>No activity recorded yet. New events will appear here automatically.</p></div>}
      </section>

      {focused && active ? (
        <FocusView
          key={focused.id}
          feed={focused}
          frame={frame}
          source={source}
          logs={focusedLogs}
          steps={focusedSteps}
          pipelineError={pipelineError}
          liveStatus={live[focused.id]?.status}
          now={now}
          onClose={() => setFocusId(null)}
        />
      ) : null}
    </div>
  );
}

function FocusView({
  feed,
  frame,
  source,
  logs,
  steps,
  pipelineError,
  liveStatus,
  now,
  onClose,
}: {
  feed: ConsoleFeed;
  frame: WallFrame | null;
  source: FeedSource | undefined;
  logs: LogEntry[];
  steps: PipelineStep[];
  pipelineError: string | null;
  liveStatus?: "idle" | "watching" | "ended" | "error";
  now: number;
  onClose: () => void;
}) {
  const state = feedState(true, source?.error, frame, liveStatus);
  const person = frame?.persons?.find((item) => item.primary) ?? frame?.persons?.[0];
  const verification = (frame?.alert ?? frame?.last_alert)?.verification;
  const verifying = verification?.status === "verifying" || verification?.status === "pending";
  const startedAt = typeof verification?.started_at === "number" ? verification.started_at : null;
  const elapsed = verifying && startedAt != null ? Math.max(0, Math.floor(now / 1000 - startedAt)) : null;
  const stage = verification?.stage === "sam" ? "SAM tracking"
    : verification?.stage === "muse" ? "Muse deciding"
      : verification?.stage === "clip" ? "Collecting clip"
        : "Checking";
  // The verdict stays on screen after the clip ends. Dropping the row the
  // moment verification finishes is what made a finished incident look stuck.
  const verificationLabel = verifying ? `${stage}${elapsed != null ? ` · ${elapsed}s` : ""}`
    : verification?.status === "notified" ? "Alert raised"
      : verification?.status === "verified" ? "Resident check-in"
      : verification?.status === "unavailable" ? "Local safety check"
        : verification?.status === "rejected" ? "Not corroborated"
          : verification?.status === "recovered" ? "Recovered"
            : verification?.status === "acknowledged" ? "Family responding"
            : null;
  return (
    <Modal className={styles.focus} labelId="focus-title" onClose={onClose}>
      <header className={styles.focusHead}>
        <div className={styles.focusIdentity}>
          <Icon name="camera" size={20} />
          <div><h2 className={styles.focusName} id="focus-title">{feed.name || feed.label}</h2>
            <p className={styles.focusLocation}>{feed.location || "Camera view"} · {feed.kind === "camera" ? "Device camera" : "Recording"}</p></div>
        </div>
        <div className={styles.focusActions}>
          <span className={styles.focusStatus} data-alert={state.alert}>{verificationLabel || state.label}</span>
          <Link href={`/deeper-look/${encodeURIComponent(feed.id)}`} className={styles.deeperLook}>
            A deeper look <Icon name="arrow" size={16} />
          </Link>
          <button type="button" className={styles.focusClose} onClick={onClose} aria-label="Close focus view">
            <Icon name="close" size={18} />
          </button>
        </div>
      </header>
      <div className={styles.focusBody}>
      <div className={styles.focusStage}>
        <div className={styles.focusVideo}>
        {source && !source.error ? (
          <LiveFeed feed={feed} source={source} frame={frame ?? undefined} active detailed />
        ) : (
          <div className={styles.tileStandby} style={{ position: "absolute" }}>
            <p><strong>{source?.error || "Opening the feed"}</strong>{feed.name}</p>
          </div>
        )}
        </div>
        <div className={styles.focusTelemetry} aria-label="Live performance">
          <span><b>{frame?.person_count ?? 0}</b> {(frame?.person_count ?? 0) === 1 ? "person" : "people"} · {person?.posture === "sitting" ? "Seated" : person?.posture === "lying" ? "Lying down" : person?.posture === "unknown" ? "Position unclear" : person?.posture ?? "Awaiting observation"}</span>
          <span title={`${Math.round(frame?.tracking.processing_ms ?? 0)} ms analysis per image`}>{liveStatus === "ended" ? "Recording complete · final frame" : `Updated ${frame?.tracking.fps || "—"} times / second`}</span>
          <span>Received <b>{timeOf(frame?.at)}</b></span>
        </div>
      </div>
      <FeedContext frame={frame} steps={steps} logs={logs} error={pipelineError} />
      </div>
    </Modal>
  );
}
