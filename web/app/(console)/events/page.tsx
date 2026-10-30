"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { Icon } from "@/components/icons";
import {
  clock,
  dayLabel,
  severityLabel,
  type Incident,
  type Severity,
  typeLabel,
  zoneLabel,
} from "@/lib/api";
import { useApi, useStream } from "@/lib/useApi";
import shared from "@/components/console-shared.module.css";
import styles from "@/components/console-pages.module.css";

type Filter = "all" | "urgent" | "watch" | "quiet";
type Scope = "today" | "all";

const FILTERS: { id: Filter; label: string }[] = [
  { id: "all", label: "All" },
  { id: "urgent", label: "Urgent" },
  { id: "watch", label: "Worth a look" },
  { id: "quiet", label: "Noted" },
];

function isToday(iso: string | null | undefined): boolean {
  if (!iso) return false;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return false;
  return date.toDateString() === new Date().toDateString();
}

function severityRank(severity: Severity | string | null | undefined): number {
  if (severity === "emergency") return 3;
  if (severity === "urgent") return 2;
  if (severity === "watch") return 1;
  return 0;
}

function feedName(incident: Incident): string {
  const sensors = incident.sensors as { wall_feed?: { feed_name?: string } } | null | undefined;
  return sensors?.wall_feed?.feed_name || zoneLabel(incident.zone_id);
}

function statusText(incident: Incident): string {
  if (incident.checkin_status === "no_answer") return "Check-in escalated";
  if (incident.checkin_status === "asking") return "Check-in waiting";
  if (incident.checkin_status === "answered") return "Check-in answered";
  if (incident.status === "resolved") return "Resolved";
  if (incident.status === "notified") return "Family notified";
  if (incident.status === "enriching") return "Gathering evidence";
  return incident.status.replace(/_/g, " ");
}

export default function EventsPage() {
  const incidents = useApi<{ items: Incident[] }>("/api/incidents?limit=60", 15000);
  const [scope, setScope] = useState<Scope>("today");
  const [filter, setFilter] = useState<Filter>("all");
  const [openId, setOpenId] = useState<string | null>(null);

  useStream((frame) => {
    if (frame.startsWith("incident.") || frame.startsWith("checkin.") || frame === "snapshot") {
      incidents.reload();
    }
  });

  const allItems = incidents.data?.items ?? [];
  const scoped = useMemo(
    () => (scope === "today"
      ? allItems.filter((item) => isToday(item.local_time || item.created_at))
      : allItems),
    [allItems, scope],
  );
  const filtered = useMemo(() => {
    if (filter === "all") return scoped;
    if (filter === "urgent") return scoped.filter((item) => severityRank(item.severity) >= 2);
    if (filter === "watch") return scoped.filter((item) => item.severity === "watch");
    return scoped.filter((item) => severityRank(item.severity) === 0);
  }, [scoped, filter]);

  const urgent = scoped.filter((item) => severityRank(item.severity) >= 2).length;
  const waiting = scoped.filter((item) => item.checkin_status === "asking").length;
  const todayCount = allItems.filter((item) => isToday(item.local_time || item.created_at)).length;

  return (
    <div className={shared.page}>
      <div className={shared.pageHead}>
        <div>
          <p className={shared.kicker}>Family-facing record</p>
          <h1 className={shared.pageTitle}>What the house decided to raise.</h1>
          <p className={shared.pageLede}>
            One row per incident, with the evidence behind it. Suppressed movement stays in the
            per-camera logs, not here — an event list that includes every kettle is a list nobody
            reads at 3 AM.
          </p>
        </div>
        <div className={shared.headStat}>
          <strong>{urgent}</strong>
          <span>urgent or emergency</span>
        </div>
      </div>

      {waiting ? (
        <div className={shared.noteBar}>
          <span>{waiting} check-in{waiting === 1 ? "" : "s"} still waiting for an answer. Silence escalates.</span>
          <Link href="/logs">Open logs</Link>
        </div>
      ) : null}

      <div className={shared.toolbar}>
        <span className={shared.homeStat}>Range</span>
        <button type="button" className={shared.filter} data-on={scope === "today"} onClick={() => { setScope("today"); setOpenId(null); }}>
          Today <span style={{ marginLeft: 5, opacity: 0.6 }}>{todayCount}</span>
        </button>
        <button type="button" className={shared.filter} data-on={scope === "all"} onClick={() => { setScope("all"); setOpenId(null); }}>
          All time <span style={{ marginLeft: 5, opacity: 0.6 }}>{allItems.length}</span>
        </button>
        <span className={shared.scopeDivider} />
        {FILTERS.map((item) => (
          <button
            key={item.id}
            type="button"
            className={shared.filter}
            data-on={filter === item.id}
            onClick={() => setFilter(item.id)}
          >
            {item.label}
            <span style={{ marginLeft: 5, opacity: 0.6 }}>
              {item.id === "all"
                ? scoped.length
                : item.id === "urgent"
                  ? scoped.filter((incident) => severityRank(incident.severity) >= 2).length
                  : item.id === "watch"
                    ? scoped.filter((incident) => incident.severity === "watch").length
                    : scoped.filter((incident) => severityRank(incident.severity) === 0).length}
            </span>
          </button>
        ))}
        <span style={{ flex: 1 }} />
        <button type="button" className={shared.ghostButton} onClick={incidents.reload}>
          Refresh
        </button>
      </div>

      {incidents.error ? (
        <div className={shared.noteBar} style={{ color: "var(--c-red)", borderColor: "var(--c-red-dim)" }}>
          <span>{incidents.error}</span>
          <button type="button" className={shared.ghostButton} onClick={incidents.reload}>Try again</button>
        </div>
      ) : null}

      {incidents.loading && !allItems.length ? (
        <div className={shared.emptyState}>
          <span className={shared.emptyStateMark}><Icon name="alerts" size={17} /></span>
          <h2>Reading the record…</h2>
        </div>
      ) : filtered.length ? (
        <div className={styles.eventList}>
          {filtered.map((incident) => {
            const open = openId === incident.id;
            const title = incident.decision?.title || incident.title || typeLabel(incident.type);
            const body = incident.decision?.body || incident.body || (
              incident.status === "enriching"
                ? "The local alert is already out. The evidence agent is still gathering context."
                : "No summary was written for this event."
            );
            return (
              <div key={incident.id} className={styles.eventRow} data-open={open}>
                <button
                  type="button"
                  className={styles.eventRowHit}
                  onClick={() => setOpenId(open ? null : incident.id)}
                  aria-expanded={open}
                >
                  <span className={styles.eventTime}>
                    <strong>{clock(incident.local_time || incident.created_at)}</strong>
                    {dayLabel(incident.local_time || incident.created_at)}
                  </span>
                  <span className={styles.eventBar} data-sev={incident.severity} />
                  <span className={styles.eventBody}>
                    <span className={styles.eventTitle}>{title}</span>
                    <span className={styles.eventText}>{body}</span>
                    <span className={styles.eventMeta}>
                      <span className={styles.sev} data-sev={incident.severity}>{severityLabel(incident.severity)}</span>
                      <span>{feedName(incident)}</span>
                      <span>{typeLabel(incident.type)}</span>
                      <span>{statusText(incident)}</span>
                      {incident.decision?.model && incident.decision.model !== "template" ? (
                        <span>verified by Muse</span>
                      ) : incident.decision ? (
                        <span>local template</span>
                      ) : null}
                    </span>
                  </span>
                  <span className={styles.eventChevron}><Icon name="chevron" size={16} /></span>
                </button>

                {open ? (
                  <div className={styles.eventDetail}>
                    <div className={styles.eventDetailGrid}>
                      <div>
                        <h4>What the evidence supports</h4>
                        <p>{body}</p>
                        {incident.decision?.rationale ? (
                          <>
                            <h4>Why it was raised</h4>
                            <p>{incident.decision.rationale}</p>
                          </>
                        ) : null}
                        {incident.decision?.evidence?.length ? (
                          <>
                            <h4>Evidence</h4>
                            <ul>
                              {incident.decision.evidence.map((item) => <li key={item}>{item}</li>)}
                            </ul>
                          </>
                        ) : null}
                        {incident.agent?.length ? (
                          <>
                            <h4>Agent trace</h4>
                            <div className={styles.evidenceTrace}>
                              {incident.agent.map((step, index) => (
                                <div className={styles.traceRow} key={`${step.step}-${index}`}>
                                  <span className={step.ok === false ? styles.bad : styles.ok}>{step.ok === false ? "×" : "·"}</span>
                                  <span className={styles.tool}>
                                    {step.tool || step.step}
                                    {step.summary ? ` — ${step.summary}` : ""}
                                  </span>
                                  <span>{step.latency_ms ? `${step.latency_ms} ms` : ""}</span>
                                </div>
                              ))}
                            </div>
                          </>
                        ) : null}
                        {incident.checkin_status ? (
                          <>
                            <h4>Check-in</h4>
                            <p>
                              {incident.checkin_prompt || "A spoken check-in was started."}
                              {" "}Outcome: {incident.checkin?.outcome?.replace(/_/g, " ") || incident.checkin_status}.
                            </p>
                          </>
                        ) : null}
                        <h4>Record</h4>
                        <p>
                          {incident.id} · severity floor {incident.policy_floor || incident.severity}
                          {incident.decision?.latency_ms ? ` · decided in ${incident.decision.latency_ms} ms` : ""}
                        </p>
                      </div>
                      <div>
                        <h4>Clip</h4>
                        {incident.has_clip ? (
                          <div className={styles.clipFrame}>
                            <video
                              controls
                              preload="metadata"
                              poster={incident.still_url}
                              src={incident.annotated_url || incident.clip_url}
                            />
                          </div>
                        ) : incident.still_url ? (
                          <div className={styles.clipFrame}>
                            {/* eslint-disable-next-line @next/next/no-img-element */}
                            <img src={incident.still_url} alt="The still kept for this incident" />
                          </div>
                        ) : (
                          <p>No clip was kept for this event.</p>
                        )}
                        <p style={{ marginTop: 8 }}>
                          <Link href={`/alerts/${incident.id}`}>Open the full alert page</Link>
                        </p>
                      </div>
                    </div>
                  </div>
                ) : null}
              </div>
            );
          })}
        </div>
      ) : (
        <div className={shared.emptyState}>
          <span className={shared.emptyStateMark}><Icon name="shield" size={17} /></span>
          <h2>
            {scope === "today"
              ? "Nothing has needed you today."
              : filter === "all"
                ? "No events yet."
                : "No events in this band."}
          </h2>
          <p>
            {scope === "today"
              ? "Switch to all time to read the household record. Suppressed movement stays in the per-camera logs."
              : filter === "all"
                ? "When the local floor raises something and verification allows a family alert, it appears here with its clip and its reasoning."
                : "Try another filter. Suppressed movement is recorded in the per-camera logs, never as an alert."}
          </p>
          <div>
            <Link className={shared.smallButton} href="/logs">Open the logs</Link>
          </div>
        </div>
      )}
    </div>
  );
}
