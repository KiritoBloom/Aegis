"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { Investigation } from "./investigation";
import type { LogEntry, PipelineStep, WallFrame } from "@/lib/console-types";
import styles from "./feed-context.module.css";

const CLOSED = new Set(["rejected", "recovered", "acknowledged", "stopped", "paused"]);
const clock = (at: number) => new Date(at).toLocaleTimeString("en-GB", { hour12: false });

/** A stable context slot: local activity gives way to this camera's current event. */
export function FeedContext({ frame, steps, logs, error }: {
  frame: WallFrame | null; steps: PipelineStep[]; logs: LogEntry[]; error?: string | null;
}) {
  const alert = frame?.alert ?? frame?.last_alert;
  const latestId = steps.at(-1)?.incident_id;
  const newLocalId = alert?.active && alert.incident_id && !steps.some((step) => step.incident_id === alert.incident_id)
    ? alert.incident_id : undefined;
  const eventId = newLocalId ?? latestId ?? alert?.incident_id;
  const rows = steps.filter((step) => step.incident_id === eventId);
  const matchingAlert = alert?.incident_id === eventId ? alert : undefined;
  const closed = CLOSED.has(matchingAlert?.verification?.status ?? "") || rows.some((step) =>
    ["response", "recovery", "recovered", "rejected", "acknowledged", "stopped", "paused"].includes(step.stage)
    || CLOSED.has(step.metrics.verdict ?? "") || step.metrics.resolution === "self_recovered");
  const pending = Boolean(eventId && !closed && (rows.length || matchingAlert?.active));
  const [presentedId, setPresentedId] = useState<string | null>(pending ? eventId! : null);
  const [visible, setVisible] = useState(pending);
  const investigationRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (pending && eventId) {
      setPresentedId(eventId);
      setVisible(true);
      return;
    }
    if (closed && presentedId === eventId && visible) {
      // Let the outcome be read before returning to quiet monitoring.
      const timer = window.setTimeout(() => setVisible(false), 8000);
      return () => window.clearTimeout(timer);
    }
  }, [pending, closed, eventId, presentedId, visible]);

  useEffect(() => { if (investigationRef.current) investigationRef.current.scrollTop = 0; }, [presentedId]);
  const presentedSteps = steps.filter((step) => step.incident_id === presentedId);

  return <aside className={styles.context} aria-label="Camera context" data-mode={visible ? "investigation" : "log"}>
    <p className={styles.announcement} role="status">{visible ? "Event detected. Showing this camera's investigation." : "Showing the feed log."}</p>
    <section className={styles.view} data-visible={!visible} aria-hidden={visible} inert={visible} aria-label="Feed log">
      <header className={styles.logHead}>
        <div><span className={styles.liveIndicator} /><h3>Feed log</h3></div>
        <Link href="/logs">All logs <span aria-hidden="true">↗</span></Link>
      </header>
      <p className={styles.logIntro}>Live observations from this camera.</p>
      {logs.length ? <ol className={styles.logList}>
        {logs.slice().reverse().map((entry) => <li key={entry.id} data-tone={entry.tone}>
          <div className={styles.logMeta}><span>{entry.source}</span><time dateTime={new Date(entry.at).toISOString()}>{clock(entry.at)}</time></div>
          <strong>{entry.message}</strong>
          {entry.detail ? <p>{entry.detail}</p> : null}
        </li>)}
      </ol> : <p className={styles.empty}>Waiting for the first observation. Camera activity will appear here.</p>}
    </section>
    <div ref={investigationRef} className={`${styles.view} ${styles.eventView}`} data-visible={visible} aria-hidden={!visible} inert={!visible}>
      {presentedSteps.length ? <Investigation key={presentedId} steps={presentedSteps} error={error} /> : presentedId ? <div className={styles.pending}>
        <span className={styles.liveIndicator} /><h3>Looking closer</h3>
        <p>{matchingAlert?.verification?.reason || "A local event was detected. Waiting for the first evidence step."}</p>
      </div> : null}
    </div>
  </aside>;
}
