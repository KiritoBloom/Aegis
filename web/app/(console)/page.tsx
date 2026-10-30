"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { Icon } from "@/components/icons";
import { LiveFeed } from "@/components/live-feed";
import { useConsole } from "@/components/console-provider";
import type { FeedSource, LogEntry, WallFrame } from "@/lib/console-types";
import type { ConsoleFeed } from "@/lib/console";
import shared from "@/components/console-shared.module.css";
import styles from "@/components/console-home.module.css";

function timeOf(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleTimeString("en-GB", { hour12: false });
}

function feedState(active: boolean, error: string | undefined, frame: WallFrame | null | undefined) {
  if (!active) return { tone: "", label: "Standby", urgent: false, alert: false };
  if (error) return { tone: "error", label: "Link lost", urgent: false, alert: false };
  const status = frame?.alert?.verification?.status;
  if (status === "verified") return { tone: "urgent", label: "Family alerted", urgent: true, alert: true };
  if (status === "unavailable") return { tone: "urgent", label: "Local alert sent", urgent: true, alert: true };
  if (status === "rejected") return { tone: "", label: "Not corroborated", urgent: false, alert: false };
  if (status === "verifying" || status === "pending") return { tone: "alert", label: "Verifying", urgent: false, alert: true };
  if (frame?.alert?.active) return { tone: "alert", label: "Local alert", urgent: false, alert: true };
  if (frame) return { tone: "live", label: "Tracking", urgent: false, alert: false };
  return { tone: "", label: "Opening", urgent: false, alert: false };
}

export default function ConsoleHome() {
  const { setup, resident, active, setActive, feeds, sources, live, logs } = useConsole();
  const [focusId, setFocusId] = useState<string | null>(null);

  const residentName = resident?.name?.trim() || "Resident";
  const focused = feeds.find((feed) => feed.id === focusId) ?? null;

  useEffect(() => {
    if (!active) setFocusId(null);
  }, [active]);

  useEffect(() => {
    if (!focused) return;
    const close = (event: KeyboardEvent) => {
      if (event.key === "Escape") setFocusId(null);
    };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [focused]);

  const frame = focused ? live[focused.id]?.frame ?? null : null;
  const source = focused ? sources[focused.id] : undefined;
  const focusedLogs = focused ? (logs[focused.id] ?? []).slice(-14) : [];

  const alertCount = useMemo(
    () => feeds.filter((feed) => live[feed.id]?.frame?.alert?.active).length,
    [feeds, live],
  );

  if (!setup) return null;

  return (
    <div className={styles.home}>
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
        <div className={shared.noteBar}>
          <span>
            The console is in standby. No frames are analyzed and no camera stream is open.
          </span>
          <button type="button" className={shared.primaryButton} onClick={() => void setActive(true)}>
            Activate system
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
            const status = active && !mapped
              ? { tone: "", label: "Mapping required", urgent: false, alert: false }
              : feedState(active, error, data);
            const person = data?.persons?.[0];
            return (
              <button
                key={feed.id}
                type="button"
                className={styles.tile}
                data-alert={status.alert}
                data-urgent={status.urgent}
                onClick={() => { if (mapped) setFocusId(feed.id); }}
                aria-disabled={active && !mapped}
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
                  <LiveFeed feed={feed} source={feedSource} frame={data} active={active} />
                )}

                <span className={styles.tileChrome}>
                  <span className={styles.tileIdent}>
                    <span className={styles.tileCam}>CAM {String(index + 1).padStart(2, "0")}</span>
                    <span className={styles.tileName}>{feed.name || feed.label}</span>
                  </span>
                  <span className={styles.tileLive} data-off={!active || Boolean(error)}>
                    <span className={styles.liveDot} />
                    {!active ? "CLOSED" : error ? "NO SIGNAL" : `${data?.tracking?.fps ?? "—"} FPS`}
                  </span>
                </span>

                <span className={styles.tileExpand} aria-hidden="true">
                  <Icon name="expand" size={13} />
                </span>

                <span className={styles.tileBottom}>
                  <span className={styles.telemetry} data-alert={status.alert} data-urgent={status.urgent}>
                    <span><b>{data?.person_count ?? 0}</b> {(data?.person_count ?? 0) === 1 ? "PERSON" : "PEOPLE"}</span>
                    <span>{person?.posture ? person.posture.toUpperCase() : "NO POSTURE"}</span>
                    {data?.tracking?.lying_for_s ? <span>DOWN <b>{Math.round(data.tracking.lying_for_s)}S</b></span> : null}
                    <span>{feed.location || "UNLABELLED"}</span>
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

      {focused && active ? (
        <FocusView
          feed={focused}
          frame={frame}
          source={source}
          logs={focusedLogs}
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
  onClose,
}: {
  feed: ConsoleFeed;
  frame: WallFrame | null;
  source: FeedSource | undefined;
  logs: LogEntry[];
  onClose: () => void;
}) {
  const state = feedState(true, source?.error, frame);
  const person = frame?.persons?.[0];
  return (
    <div className={styles.focus} role="dialog" aria-modal="true" aria-labelledby="focus-title">
      <div className={styles.focusStage}>
        {source && !source.error ? (
          <LiveFeed feed={feed} source={source} frame={frame ?? undefined} active />
        ) : (
          <div className={styles.tileStandby} style={{ position: "absolute" }}>
            <p><strong>{source?.error || "Opening the feed"}</strong>{feed.name}</p>
          </div>
        )}
      </div>
      <aside className={styles.focusSide}>
        <div className={styles.focusHead}>
          <div>
            <p className={styles.focusKicker}>Camera focus</p>
            <h2 className={styles.focusName} id="focus-title">{feed.name || feed.label}</h2>
            <p className={styles.focusLocation}>{feed.location || "No room label"} · {feed.kind}</p>
          </div>
          <button type="button" className={styles.focusClose} onClick={onClose} aria-label="Close focus view">
            <Icon name="close" size={16} />
          </button>
        </div>
        <div className={shared.focusFacts}>
          <div className={shared.focusFact}><span>State</span><strong>{state.label}</strong></div>
          <div className={shared.focusFact}><span>People</span><strong>{frame?.person_count ?? 0}</strong></div>
          <div className={shared.focusFact}><span>Posture</span><strong>{person?.posture ?? "none"}</strong></div>
          <div className={shared.focusFact}><span>Frame rate</span><strong>{frame?.tracking?.fps ?? "—"} fps</strong></div>
          <div className={shared.focusFact}><span>Last frame</span><strong>{timeOf(frame?.at)}</strong></div>
          <div className={shared.focusFact}><span>Down for</span><strong>{frame?.tracking?.lying_for_s ? `${Math.round(frame.tracking.lying_for_s)}s` : "—"}</strong></div>
        </div>
        <div className={styles.focusLogs}>
          <div className={styles.focusLogsHead}>
            <span>Feed log</span>
            <Link href="/logs">All logs</Link>
          </div>
          {logs.length ? (
            logs.slice().reverse().map((entry) => (
              <div key={entry.id} className={shared.logRow}>
                <span className={shared.logTime}>{timeOf(new Date(entry.at).toISOString())}</span>
                <span className={shared.logSource} data-tone={entry.tone}>{entry.source}</span>
                <span className={shared.logMessage}>
                  {entry.message}
                  {entry.detail ? <span className={shared.logDetail}>{entry.detail}</span> : null}
                </span>
              </div>
            ))
          ) : (
            <p className={shared.logEmpty}>No local activity on this feed yet.</p>
          )}
        </div>
      </aside>
    </div>
  );
}
