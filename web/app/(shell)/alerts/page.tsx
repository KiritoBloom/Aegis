"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { Empty, SeverityChip, Skeleton } from "@/components/ui";
import {
  clock,
  dayLabel,
  typeLabel,
  zoneLabel,
  type AegisEvent,
  type Incident,
} from "@/lib/api";
import { useApi, useStream } from "@/lib/useApi";

/**
 * Alerts, and the things that were deliberately not alerts.
 *
 * The second half is the point. A list of only the interruptions would make
 * Aegis look like a motion detector that happens to be quiet; showing the
 * suppressed events next to them is what makes the restraint counter believable.
 * Every suppressed row carries the reason it was held, in plain words.
 */
type Filter = "all" | "urgent" | "watch" | "held";

export default function AlertsPage() {
  const incidents = useApi<{ items: Incident[] }>("/api/incidents?limit=60", 20000);
  const events = useApi<{ items: AegisEvent[]; suppressed: AegisEvent[] }>("/api/events", 20000);
  const [filter, setFilter] = useState<Filter>("all");

  useStream((frame) => {
    if (frame.startsWith("incident.") || frame === "snapshot") {
      incidents.reload();
      events.reload();
    }
  });

  const items = incidents.data?.items ?? [];
  const held = events.data?.suppressed ?? [];

  const shown = useMemo(() => {
    if (filter === "urgent") return items.filter((i) => i.severity === "urgent" || i.severity === "emergency");
    if (filter === "watch") return items.filter((i) => i.severity === "watch");
    return items;
  }, [items, filter]);

  const groups = useMemo(() => {
    const out: { label: string; rows: Incident[] }[] = [];
    for (const inc of shown) {
      const label = dayLabel(inc.local_time || inc.created_at) || "Earlier";
      const last = out[out.length - 1];
      if (last && last.label === label) last.rows.push(inc);
      else out.push({ label, rows: [inc] });
    }
    return out;
  }, [shown]);

  return (
    <div style={{ maxWidth: 820 }}>
      <header style={{ marginBottom: 20 }}>
        <p className="eyebrow">Alerts</p>
        <h1 className="h1" style={{ margin: "8px 0 0" }}>Everything that reached you.</h1>
        <p className="lede" style={{ marginTop: 10, maxWidth: 560 }}>
          {items.length
            ? `${items.length} alert${items.length === 1 ? "" : "s"} kept. ${held.length} other thing${held.length === 1 ? "" : "s"} were noticed and stayed quiet.`
            : "Nothing has needed you yet."}
        </p>
      </header>

      <div role="tablist" aria-label="Filter alerts" style={{ display: "flex", gap: 6, marginBottom: 18, flexWrap: "wrap" }}>
        {(
          [
            ["all", `All ${items.length}`],
            ["urgent", "Urgent"],
            ["watch", "Worth a look"],
            ["held", `Stayed quiet ${held.length}`],
          ] as const
        ).map(([key, label]) => (
          <button
            key={key}
            role="tab"
            aria-selected={filter === key}
            className="btn"
            style={{
              minHeight: 34,
              fontSize: 13,
              padding: "0 12px",
              background: filter === key ? "var(--color-ink)" : "transparent",
              color: filter === key ? "#fbf9f5" : "var(--color-ink-2)",
              borderColor: filter === key ? "var(--color-ink)" : "var(--color-line)",
            }}
            onClick={() => setFilter(key)}
          >
            {label}
          </button>
        ))}
      </div>

      {filter === "held" ? (
        <HeldList held={held} loading={events.loading} />
      ) : incidents.loading && !incidents.data ? (
        <div style={{ display: "grid", gap: 10 }}>
          <Skeleton h={64} />
          <Skeleton h={64} />
          <Skeleton h={64} />
        </div>
      ) : shown.length === 0 ? (
        <Empty title="No alerts. The house is quiet.">
          When something does need you, it appears here with the clip, the reason,
          and what it cost to send.
        </Empty>
      ) : (
        <div style={{ display: "grid", gap: 26 }}>
          {groups.map((g) => (
            <section key={g.label}>
              <p className="eyebrow" style={{ marginBottom: 8 }}>{g.label}</p>
              <div className="card" style={{ overflow: "hidden" }}>
                {g.rows.map((inc, i) => (
                  <Link
                    key={inc.id}
                    href={`/alerts/${inc.id}`}
                    className="row-link"
                    style={{
                      display: "grid",
                      gridTemplateColumns: "3px 1fr auto",
                      gap: 14,
                      alignItems: "center",
                      padding: "14px 18px",
                      borderTop: i === 0 ? "none" : "1px solid var(--color-line)",
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
                        {clock(inc.local_time || inc.created_at)} · {zoneLabel(inc.zone_id)}
                        {inc.has_clip ? " · clip" : ""}
                        {inc.source === "sim" ? <span className="chip chip-sim" style={{ marginLeft: 8 }}>simulated</span> : null}
                        {inc.status === "acknowledged" ? <span className="chip" style={{ marginLeft: 8 }}>acknowledged</span> : null}
                      </span>
                    </span>
                    <SeverityChip severity={inc.severity} />
                  </Link>
                ))}
              </div>
            </section>
          ))}
        </div>
      )}
    </div>
  );
}

function HeldList({ held, loading }: { held: AegisEvent[]; loading: boolean }) {
  if (loading && held.length === 0) {
    return (
      <div style={{ display: "grid", gap: 8 }}>
        <Skeleton h={44} />
        <Skeleton h={44} />
      </div>
    );
  }
  if (held.length === 0) {
    return (
      <Empty title="Nothing has been held back yet.">
        Once the camera has watched for a day, the things it decided were none of
        your business appear here — with the reason for each one.
      </Empty>
    );
  }
  return (
    <div>
      <p className="small" style={{ margin: "0 0 14px", maxWidth: 560 }}>
        These happened. Aegis counted them, logged why it stayed quiet, and did not
        wake anyone. Nothing here was deleted.
      </p>
      <div className="card" style={{ overflow: "hidden" }}>
        {held.map((ev, i) => (
          <div
            key={`${ev.id}-${i}`}
            style={{
              display: "grid",
              gridTemplateColumns: "1fr auto",
              gap: 12,
              alignItems: "center",
              padding: "12px 18px",
              borderTop: i === 0 ? "none" : "1px solid var(--color-line)",
            }}
          >
            <div style={{ minWidth: 0 }}>
              <p className="h3" style={{ margin: 0 }}>{typeLabel(ev.type)}</p>
              <p className="micro" style={{ margin: "2px 0 0" }}>
                {clock(ev.ts)} · {zoneLabel(ev.zone_id)} · {reasonText(ev.reason)}
              </p>
            </div>
            <span className="mono micro" style={{ color: "var(--color-ink-3)" }}>quiet</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function reasonText(reason: string | null | undefined): string {
  if (!reason) return "not an alert";
  const r = reason.toLowerCase();
  if (r.includes("muted_by_household")) return "you asked not to be told";
  if (r.includes("held_for_digest")) return "held for the morning letter";
  if (r.includes("monitoring_paused")) return "monitoring was paused";
  if (r.includes("false_alarm_cooldown")) return "you marked this a false alarm recently";
  if (r.includes("recurring_ritual")) return "a daily ritual, not a fall";
  if (r.includes("recovered")) return "she got up again";
  if (r.includes("caregiver") || r.includes("second_person")) return "someone was with her";
  if (r.includes("rest")) return "resting, where resting is normal";
  if (r.includes("approach_only")) return "someone at the door, nothing more";
  if (r.includes("person_in_kitchen")) return "she was still in the kitchen";
  if (r.includes("no_rule")) return "nothing it has a rule for";
  return reason.split(",")[0].replace(/_/g, " ");
}
