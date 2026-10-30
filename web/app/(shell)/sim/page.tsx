"use client";

import Link from "next/link";
import { useState } from "react";
import { Icon } from "@/components/icons";
import { Banner, Fact, Skeleton } from "@/components/ui";
import { send, type Stats } from "@/lib/api";
import { useApi } from "@/lib/useApi";

/** Control room for deterministic pipeline scenarios. */

type Scenario = {
  id: string;
  label: string;
  detail: string;
  group: "door" | "fall" | "kitchen" | "voice" | "night" | "quiet" | "patterns";
  expect: string;
};

const SCENARIOS: Scenario[] = [
  {
    id: "door",
    label: "Door opens",
    detail: "The front door is open and she is in the doorway.",
    group: "door",
    expect: "Urgent at night with dementia. Worth a look by day.",
  },
  {
    id: "door_left_open",
    label: "Door left open",
    detail: "Four minutes later the door is still open.",
    group: "door",
    expect: "A quiet check — open doors are only alarming when they stay open.",
  },
  {
    id: "fall",
    label: "Fall, no recovery",
    detail: "Down in the hallway, twelve seconds and not moving.",
    group: "fall",
    expect: "Emergency. This is the one that must always reach a phone.",
  },
  {
    id: "fall_recovered",
    label: "Fall, got up",
    detail: "Down for four seconds, then up and moving.",
    group: "fall",
    expect: "Still urgent, but the words change: she is on her feet.",
  },
  {
    id: "fall_assisted",
    label: "Fall with help",
    detail: "A second person is already with her.",
    group: "fall",
    expect: "Urgent, not emergency — someone is there.",
  },
  {
    id: "restlessness",
    label: "Repeated pacing",
    detail: "A long path, nine sharp turns, little net movement.",
    group: "patterns",
    expect: "A care note, not an alarm. The Evidence Agent checks whether it is new.",
  },
  {
    id: "restlessness_night",
    label: "Pacing after dark",
    detail: "The same pattern at 03:00.",
    group: "patterns",
    expect: "A quiet after-dark check; a human decides what it means.",
  },
  {
    id: "prayer",
    label: "Prayer",
    detail: "Repeated posture changes, same time every day.",
    group: "fall",
    expect: "Nothing. A recognised daily ritual, counted and held.",
  },
  {
    id: "stove",
    label: "Stove, 7 minutes",
    detail: "The hob is on and nobody is in the kitchen.",
    group: "kitchen",
    expect: "Urgent.",
  },
  {
    id: "stove_long",
    label: "Stove, 12 minutes + smoke",
    detail: "Long enough, and something is burning.",
    group: "kitchen",
    expect: "Emergency.",
  },
  {
    id: "still",
    label: "Still on the sofa",
    detail: "Thirteen hours without moving much.",
    group: "quiet",
    expect: "A note in the morning, not a buzz at night.",
  },
  {
    id: "pet",
    label: "The cat",
    detail: "Motion in the hallway with no person in it.",
    group: "quiet",
    expect: "Nothing at all.",
  },
  {
    id: "caregiver",
    label: "Caregiver visit",
    detail: "The door opens and a second person is expected.",
    group: "quiet",
    expect: "Worth a look at most.",
  },
  {
    id: "camera_offline",
    label: "Camera offline",
    detail: "The camera stops sending frames.",
    group: "quiet",
    expect: "A red banner. It never pretends the house is quiet.",
  },
  {
    id: "help_call",
    label: "A cry for help",
    detail: "A call for help, no television or radio on.",
    group: "voice",
    expect: "Urgent. The alert goes out, then Aegis asks her if she is okay.",
  },
  {
    id: "help_call_tv",
    label: "A cry over the TV",
    detail: "The same cry, but the television is on and she is in frame.",
    group: "voice",
    expect: "Ambiguous. Muse reads the room and Aegis asks her before it escalates.",
  },
  {
    id: "help_call_floor",
    label: "A cry from the floor",
    detail: "She called for help and has not got up.",
    group: "voice",
    expect: "Emergency. The camera evidence outranks the television.",
  },
  {
    id: "help_call_tv_only",
    label: "The television",
    detail: "A scream-like sound, the TV is on, and no person is in frame.",
    group: "voice",
    expect: "Stay quiet. The sound is the television until a person appears.",
  },
  {
    id: "night_visitor",
    label: "Second person after dark",
    detail: "Two people at the front door at 03:00, and no expected visitor.",
    group: "night",
    expect: "Urgent. The family gets the clip, the time and the door state — not an identity.",
  },
  {
    id: "night_visitor_expected",
    label: "Expected night visitor",
    detail: "Two people at 03:00, but the plan expects a caregiver.",
    group: "night",
    expect: "Stay quiet. The second person is expected, and the event is still recorded.",
  },
  {
    id: "night_visitor_day",
    label: "Visitor in daylight",
    detail: "Two people at 14:00 with the door open.",
    group: "night",
    expect: "Stay quiet. A daylight visitor is not an alarm.",
  },
];

const GROUPS: { id: Scenario["group"]; label: string }[] = [
  { id: "door", label: "The door" },
  { id: "fall", label: "Falls and posture" },
  { id: "kitchen", label: "The kitchen" },
  { id: "voice", label: "A voice in the room" },
  { id: "night", label: "After dark" },
  { id: "quiet", label: "Things that should not wake you" },
  { id: "patterns", label: "Movement patterns" },
];

const HOURS = [3, 8, 14, 19, 23];

export default function SimPage() {
  const stats = useApi<Stats>("/api/stats", 0);
  const [busy, setBusy] = useState<string | null>(null);
  const [hour, setHour] = useState<number | null>(null);
  const [log, setLog] = useState<
    { id: string; scenario: string; floor: string; plan: string | null; at: string }[]
  >([]);
  const [error, setError] = useState<string | null>(null);

  const fire = async (s: Scenario) => {
    setBusy(s.id);
    setError(null);
    try {
      const res = await send<{ id: string; floor: string; plan: string | null }>("/api/sim/trigger", {
        scenario: s.id,
        hour,
      });
      setLog((prev) => [
        {
          id: res.id,
          scenario: s.id,
          floor: res.floor,
          plan: res.plan,
          at: new Date().toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", second: "2-digit" }),
        },
        ...prev,
      ].slice(0, 12));
      stats.reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "The scenario did not run");
    } finally {
      setBusy(null);
    }
  };

  return (
    <div style={{ maxWidth: 960 }}>
      <header style={{ marginBottom: 20 }}>
        <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: 14 }}>
          <p className="eyebrow">Control room</p>
          <Link href="/night" className="mono micro" style={{ textDecoration: "underline" }}>
            Night watch →
          </Link>
        </div>
        <h1 className="h1" style={{ margin: "8px 0 0" }}>Make something happen.</h1>
        <p className="lede" style={{ marginTop: 10, maxWidth: 600 }}>
          Each of these posts the same packet the camera posts. The policy floor, the
          person check and the model all run for real — so if a scenario behaves
          wrongly here, it would behave wrongly at 3 AM.
        </p>
        <div className="control-note">
          <span className="dot dot-live" aria-hidden="true" />
          Simulated input · real policy · real model calls when a clip exists
        </div>
      </header>

      <div className="card-flat" style={{ padding: 16, marginBottom: 16 }}>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 16, alignItems: "center", justifyContent: "space-between" }}>
          <div>
            <p className="eyebrow" style={{ margin: 0 }}>Time of day</p>
            <p className="small" style={{ margin: "4px 0 0" }}>
              The incident keeps its own clock, so the night rules apply for real.
            </p>
          </div>
          <div role="radiogroup" aria-label="Time of day" style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            <button
              type="button"
              className="btn"
              role="radio"
              aria-checked={hour === null}
              onClick={() => setHour(null)}
              style={{
                minHeight: 36,
                padding: "0 12px",
                fontSize: 13,
                background: hour === null ? "var(--color-ink)" : "transparent",
                color: hour === null ? "#fbf9f5" : "var(--color-ink-2)",
                borderColor: hour === null ? "var(--color-ink)" : "var(--color-line-2)",
              }}
            >
              Now
            </button>
            {HOURS.map((h) => (
              <button
                key={h}
                type="button"
                className="btn mono"
                role="radio"
                aria-checked={hour === h}
                onClick={() => setHour(h)}
                style={{
                  minHeight: 36,
                  padding: "0 12px",
                  fontSize: 13,
                  background: hour === h ? "var(--color-ink)" : "transparent",
                  color: hour === h ? "#fbf9f5" : "var(--color-ink-2)",
                  borderColor: hour === h ? "var(--color-ink)" : "var(--color-line-2)",
                }}
              >
                {String(h).padStart(2, "0")}:00
              </button>
            ))}
          </div>
        </div>
      </div>

      {error ? <div style={{ marginBottom: 14 }}><Banner tone="red">{error}</Banner></div> : null}

      <div style={{ display: "grid", gap: 22 }}>
        {GROUPS.map((g) => (
          <section key={g.id} className="reveal">
            <p className="eyebrow" style={{ marginBottom: 8 }}>{g.label}</p>
            <div style={{ display: "grid", gap: 10 }} className="sm:grid-cols-2">
              {SCENARIOS.filter((s) => s.group === g.id).map((s) => (
                <button
                  key={s.id}
                  type="button"
                  className="card scenario-card"
                  onClick={() => fire(s)}
                  disabled={busy !== null}
                  style={{
                    opacity: busy && busy !== s.id ? 0.55 : 1,
                  }}
                >
                  <span style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
                    <span className="h3">{s.label}</span>
                    <span className="mono micro" style={{ color: busy === s.id ? "var(--color-sage)" : "var(--color-ink-3)" }}>
                      {busy === s.id ? "running…" : "run"}
                    </span>
                  </span>
                  <span className="small">{s.detail}</span>
                  <span className="micro" style={{ color: "var(--color-ink-3)" }}>{s.expect}</span>
                </button>
              ))}
            </div>
          </section>
        ))}
      </div>

      <section style={{ marginTop: 26 }}>
        <p className="eyebrow" style={{ marginBottom: 8 }}>This session</p>
        {log.length === 0 ? (
          <p className="small" style={{ margin: 0 }}>
            Nothing fired yet. Start with <strong>Door opens</strong> at 03:00 — it is the
            clearest thirty seconds of the whole product.
          </p>
        ) : (
          <div className="card" style={{ padding: 0 }}>
            {log.map((l, i) => (
              <Link
                key={l.id}
                href={`/alerts/${l.id}`}
                className="row-link"
                style={{
                  display: "grid",
                  gridTemplateColumns: "auto 1fr auto",
                  gap: 12,
                  alignItems: "center",
                  padding: "11px 16px",
                  borderTop: i === 0 ? "none" : "1px solid var(--color-line)",
                }}
              >
                <span className="mono micro">{l.at}</span>
                <span className="small">
                  {SCENARIOS.find((s) => s.id === l.scenario)?.label ?? l.scenario}
                  {l.plan ? <span className="mono micro"> · held: {l.plan.replace(/_/g, " ")}</span> : null}
                </span>
                <span className="mono micro" style={{ display: "flex", alignItems: "center", gap: 6 }}>
                  {l.floor}
                  <Icon name="arrow" size={14} />
                </span>
              </Link>
            ))}
          </div>
        )}
      </section>

      <section style={{ marginTop: 26 }}>
        <p className="eyebrow" style={{ marginBottom: 8 }}>What it has done so far</p>
        {stats.loading && !stats.data ? (
          <Skeleton h={90} r={12} />
        ) : (
          <div className="card" style={{ padding: 18 }}>
            <Fact label="Things noticed" value={stats.data?.events_seen ?? 0} />
            <Fact label="Alerts that reached a phone" value={stats.data?.alerts_sent ?? 0} />
            <Fact label="Stayed quiet" value={stats.data?.stayed_home ?? 0} />
            <Fact
              label="Median decision"
              value={stats.data?.latency_ms ? `${(stats.data.latency_ms.p50 / 1000).toFixed(1)}s` : "—"}
            />
            <Fact
              label="Slowest decision"
              value={stats.data?.latency_ms ? `${(stats.data.latency_ms.p95 / 1000).toFixed(1)}s` : "—"}
            />
            <Fact label="Degraded decisions" value={stats.data?.degraded_decisions ?? 0} />
          </div>
        )}
      </section>

      <p className="micro" style={{ marginTop: 22, maxWidth: 560 }}>
        Every incident fired here is marked <span className="chip chip-sim">simulated</span> on
        the alert itself, in the list and in the ledger. A demo that cannot tell its own
        fiction from its own data is not a demo, it is a liability.
      </p>
    </div>
  );
}
