import { clock, typeLabel, type AegisEvent, type NightSummary, type NightView } from "@/lib/api";

const TICKS = [
  { at: 0, label: "22:00" },
  { at: 25, label: "00:00" },
  { at: 50, label: "02:00" },
  { at: 75, label: "04:00" },
  { at: 100, label: "06:00" },
];

export function nightHeadline(summary: NightSummary): { title: string; detail: string } {
  if (summary.alerts > 0) {
    return {
      title: "The night needed you.",
      detail: `${summary.alerts} ${summary.alerts === 1 ? "alert" : "alerts"} reached a phone between 22:00 and 06:00.`,
    };
  }
  if (summary.unexpected > 0) {
    return {
      title: "Someone was there. No one was woken.",
      detail: "A second person was recorded after dark, but the night plan treated them as expected.",
    };
  }
  if (summary.events > 0) {
    return {
      title: "The house moved. You were not needed.",
      detail: `${summary.events} ${summary.events === 1 ? "event was" : "events were"} recorded, and ${summary.quiet} stayed quiet.`,
    };
  }
  return {
    title: "The night left no record.",
    detail: "No door, fall, check-in or presence events were recorded in this window.",
  };
}

function dotPercent(ts: string, start: string, end: string): number | null {
  const t = Date.parse(ts);
  const a = Date.parse(start);
  const b = Date.parse(end);
  if (!Number.isFinite(t) || !Number.isFinite(a) || !Number.isFinite(b) || b <= a) return null;
  return Math.max(0, Math.min(100, ((t - a) / (b - a)) * 100));
}

function shortEvent(ev: AegisEvent): string {
  return `${clock(ev.ts)} · ${typeLabel(ev.type)}`;
}

export function NightStrip({
  view,
  compact = false,
}: {
  view: NightView;
  compact?: boolean;
}) {
  const events = [...view.events]
    .filter((event) => {
      const type = String(event.type);
      return !["motion_only", "pet_motion", "normal_rest", "prayer_activity"].includes(type);
    })
    .sort((a, b) => Date.parse(a.ts) - Date.parse(b.ts))
    .slice(0, compact ? 14 : 40);

  return (
    <div className={`night-strip ${compact ? "night-strip-compact" : ""}`} aria-label="Events between 22:00 and 06:00">
      <div className="night-axis" aria-hidden="true">
        {TICKS.map((tick) => (
          <span key={tick.label} style={{ left: `${tick.at}%` }}>
            {tick.label}
          </span>
        ))}
      </div>
      <div className="night-track" aria-hidden="true">
        {events.length === 0 ? <span className="night-empty">Nothing recorded</span> : null}
        {events.map((ev, i) => {
          const pct = dotPercent(ev.ts, view.start, view.end);
          if (pct === null) return null;
          const quiet = Boolean(ev.suppressed);
          return (
            <span
              key={`${ev.id ?? "ev"}-${i}`}
              className="night-dot"
              data-severity={ev.severity}
              data-quiet={quiet ? "true" : undefined}
              style={{ left: `${pct}%`, top: `${9 + (i % 3) * 7}px` }}
              title={`${shortEvent(ev)}${quiet ? " · stayed quiet" : ""}`}
            />
          );
        })}
      </div>
      <div className="night-legend">
        <span className="night-legend-item"><span className="night-legend-dot" data-severity="watch" /> something noticed</span>
        <span className="night-legend-item"><span className="night-legend-dot" data-severity="urgent" /> reached a phone</span>
        <span className="night-legend-item"><span className="night-legend-dot" data-quiet="true" /> stayed quiet</span>
      </div>
    </div>
  );
}
