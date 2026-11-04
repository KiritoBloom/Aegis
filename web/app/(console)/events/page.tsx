"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { Icon } from "@/components/icons";
import {
  clock,
  dayLabel,
  severityLabel,
  type Incident,
  type AegisEvent,
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
  if (["resolved", "cancelled", "acknowledged"].includes(incident.status)) return incident.status;
  if (incident.checkin_status === "no_answer") return "Check-in unanswered";
  if (incident.checkin_status === "asking") return "Check-in waiting";
  if (incident.checkin_status === "answered") return "Check-in answered";
  if (incident.status === "resolved") return "Resolved";
  if (incident.status === "notified") return "Alert raised";
  if (incident.status === "enriching") return "Gathering evidence";
  return incident.status.replace(/_/g, " ");
}

function isRejectedWallCandidate(incident: Incident): boolean {
  const sensors = incident.sensors as { verification?: string } | null | undefined;
  return incident.source === "wall"
    && incident.status === "resolved"
    && sensors?.verification === "rejected";
}

export default function EventsPage() {
  const [limit, setLimit] = useState(100);
  const incidents = useApi<{ items: Incident[] }>(`/api/incidents?limit=${limit}`, 15000);
  const [view, setView] = useState("alerts");
  const [scope, setScope] = useState<Scope>("all");
  const [filter, setFilter] = useState<Filter>("all");
  const [openId, setOpenId] = useState<string | null>(null);
  useEffect(() => { if (new URLSearchParams(window.location.search).get("view") === "activity") setView("activity"); }, []);

  useStream((frame) => {
    if (frame.startsWith("incident.") || frame.startsWith("checkin.") || frame === "snapshot") {
      incidents.reload();
    }
  });

  const allItems = useMemo(
    () => (incidents.data?.items ?? []).filter((item) => !isRejectedWallCandidate(item)),
    [incidents.data],
  );
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

  const urgent = scoped.filter((item) => severityRank(item.severity) >= 2 && !["resolved", "cancelled", "acknowledged"].includes(item.status)).length;
  const waiting = scoped.filter((item) => item.checkin_status === "asking").length;
  const todayCount = allItems.filter((item) => isToday(item.local_time || item.created_at)).length;

  return (
    <div className={shared.page}>
      <div className={shared.pageHead}>
        <div>
          <p className={shared.kicker}>Household history</p>
          <h1 className={shared.pageTitle}>Events &amp; activity</h1>
          <p className={shared.pageLede}>
            Review alerts, responses, and the activity Aegis handled quietly. Your history is saved on the local backend.
          </p>
        </div>
        <div className={shared.headStat}>
          <strong>{urgent}</strong>
          <span>need attention</span>
        </div>
      </div>

      <div className={styles.viewTabs} aria-label="Record view">
        <button className={styles.viewTab} aria-pressed={view === "alerts"} onClick={() => setView("alerts")}>Alerts <span>{allItems.length}</span></button>
        <button className={styles.viewTab} aria-pressed={view === "activity"} onClick={() => setView("activity")}>Activity log</button>
      </div>
      {view === "activity" ? <ActivityLog /> : <>
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
          Recent history <span style={{ marginLeft: 5, opacity: 0.6 }}>{allItems.length}</span>
        </button>
        <span className={shared.scopeDivider} />
        {FILTERS.map((item) => (
          <button
            key={item.id}
            type="button"
            className={shared.filter}
            data-on={filter === item.id}
            aria-pressed={filter === item.id}
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
                         <span>evidence reviewed</span>
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
                                    {step.question ? ` — “${step.question}”` : ""}
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
                              src={incident.annotated_url || incident.redacted_url || incident.clip_url}
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
               ? "Switch to Recent history, or open the Activity log to see quiet events."
              : filter === "all"
                ? "When the local floor raises something and verification allows a family alert, it appears here with its clip and its reasoning."
                 : "Try another filter, or open the Activity log for quiet events."}
          </p>
          <div>
            <Link className={shared.smallButton} href="/logs">Open the logs</Link>
          </div>
        </div>
      )}
      {allItems.length >= limit && limit < 500 ? <button className={shared.smallButton} onClick={() => setLimit((n) => Math.min(500, n + 100))}>Load older alerts</button> : null}
      </>}
    </div>
  );
}

function ActivityLog() {
  const [limit, setLimit] = useState(100);
  const [query, setQuery] = useState("");
  const [quietOnly, setQuietOnly] = useState(false);
  const events = useApi<{ items: AegisEvent[] }>(`/api/events?limit=${limit}`, 15000);
  useStream((frame) => {
    if (frame === "snapshot" || frame.startsWith("incident.") || frame.startsWith("checkin.") || frame.startsWith("privacy.")) events.reload();
  });
  const items = (events.data?.items ?? []).filter((event) => (!quietOnly || event.suppressed)
    && `${typeLabel(event.type)} ${zoneLabel(event.zone_id)} ${event.source || ""} ${event.reason || ""}`.toLowerCase().includes(query.toLowerCase()));
  return <>
    <div className={shared.toolbar}>
      <label className={styles.searchField}><span>Search activity</span><input type="search" placeholder="Event, room, or outcome" value={query} onChange={(event) => setQuery(event.target.value)} /></label>
      <button className={shared.filter} aria-pressed={quietOnly} data-on={quietOnly} onClick={() => setQuietOnly(!quietOnly)}>Quiet events only</button>
      <button className={shared.ghostButton} onClick={events.reload}>Refresh activity</button>
    </div>
    {events.error ? <div className={shared.noteBar} role="alert"><span>Activity could not be loaded: {events.error}</span><button className={shared.smallButton} onClick={events.reload}>Try again</button></div> : null}
    {events.loading && !events.data ? <div className="skeleton-block" aria-label="Loading activity" /> : items.length ? <div className={styles.activityList}>
      {items.map((event, index) => <article className={styles.activityRow} key={event.id ?? index}>
        <div className={styles.eventTime}><strong>{clock(event.ts)}</strong>{dayLabel(event.ts)}</div>
        <span className={styles.eventBar} data-sev={event.suppressed ? "info" : event.severity} />
        <div><h2 className={styles.eventTitle}>{typeLabel(event.type)}</h2>
          <p className={styles.eventText}>{event.reason ? event.reason.replace(/_/g, " ") : event.suppressed ? "Handled without an alert" : "Recorded by Aegis"}</p>
          <div className={styles.eventMeta}><span>{zoneLabel(event.zone_id)}</span><span>{event.source || "local"}</span><span>{event.suppressed ? "Quiet" : severityLabel(event.severity)}</span></div>
        </div>
        {event.incident_available && event.incident_id ? <Link className={shared.smallButton} href={`/alerts/${event.incident_id}`}>Details</Link> : <span className={styles.auditLabel}>Activity only</span>}
      </article>)}
    </div> : !events.error ? <div className={shared.emptyState}><Icon name="logs" size={24} /><h2>{query || quietOnly ? "No matching activity" : "No activity recorded yet"}</h2><p>New events, quiet outcomes, recoveries, and family responses appear here automatically.</p></div> : null}
    {(events.data?.items.length ?? 0) >= limit && limit < 500 ? <button className={shared.smallButton} onClick={() => setLimit((n) => Math.min(500, n + 100))}>Load older activity</button> : null}
  </>;
}
