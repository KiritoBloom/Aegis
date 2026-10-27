"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  Banner,
  Fact,
  Icon,
  SeverityBar,
  SeverityChip,
  Skeleton,
} from "@/components/ui";
import {
  ago,
  bytes,
  clock,
  dayLabel,
  severityLabel,
  typeLabel,
  zoneLabel,
  type Incident,
} from "@/lib/api";
import { useApi } from "@/lib/useApi";
import { canRecord, startRecording, type Recorder } from "@/lib/recorder";

/**
 * One alert, in the order a frightened person needs it.
 *
 * Severity and time, then the clip, then what happened step by step, then why
 * it was sent, then the three buttons. Nothing above the fold asks the reader to
 * interpret a chart, and the "why" is prose plus evidence chips rather than a
 * confidence score, because a number between 0 and 1 has never once helped
 * anybody decide whether to ring their mother.
 */
export default function AlertDetail() {
  const params = useParams<{ id: string }>();
  const id = params?.id ?? "";
  const { data: inc, error, loading, reload } = useApi<Incident>(`/api/incidents/${id}`, 8000);
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [view, setView] = useState<"clean" | "boxes" | "mask">("boxes");
  const [showRaw, setShowRaw] = useState(false);

  const act = useCallback(
    async (action: string) => {
      setBusy(action);
      setNote(null);
      try {
        const res = await fetch(`/api/incidents/${id}/ack`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ action }),
        });
        if (!res.ok) throw new Error(`Could not record that (${res.status})`);
        setNote(actionCopy(action));
        reload();
      } catch (err) {
        setNote(err instanceof Error ? err.message : "Could not record that");
      } finally {
        setBusy(null);
      }
    },
    [id, reload],
  );

  if (error) {
    return (
      <div style={{ maxWidth: 720 }}>
        <Link href="/alerts" className="mono micro" style={{ textDecoration: "underline" }}>
          ← All alerts
        </Link>
        <h1 className="h1" style={{ margin: "14px 0 0" }}>This alert is not here.</h1>
        <p className="lede" style={{ marginTop: 10 }}>
          It may have been deleted under the retention policy, or the link is wrong.
          Nothing about this alert is stored anywhere else.
        </p>
        <p className="mono micro" style={{ marginTop: 12 }}>{error}</p>
      </div>
    );
  }

  if (loading && !inc) {
    return (
      <div style={{ maxWidth: 820, display: "grid", gap: 14 }}>
        <Skeleton h={14} w={90} />
        <Skeleton h={34} w="65%" />
        <Skeleton h={280} r={12} />
        <Skeleton h={90} />
      </div>
    );
  }

  if (!inc) return null;

  const decision = inc.decision;
  const severity = (decision?.severity || inc.severity || "info") as string;
  const title = decision?.title || inc.title || typeLabel(inc.type);
  const body = decision?.body || inc.body || "";
  const at = inc.local_time || inc.created_at || "";
  const clip = view === "clean" ? inc.clip_url : view === "mask" ? inc.clip_url : inc.annotated_url || inc.clip_url;
  const acked = (inc.acks ?? []).length > 0;
  const sim = inc.source === "sim";

  return (
    <div style={{ maxWidth: 860, paddingBottom: 90 }}>
      <Link href="/alerts" className="mono micro" style={{ textDecoration: "underline" }}>
        ← All alerts
      </Link>

      {/* severity + title + time, one line each */}
      <header style={{ margin: "14px 0 20px" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
          <SeverityChip severity={severity} />
          {sim ? <span className="chip chip-sim">simulated</span> : null}
          {acked ? <span className="chip">acknowledged</span> : null}
          {inc.status === "resolved" ? <span className="chip chip-sage">resolved</span> : null}
        </div>
        <h1 className="h1" style={{ margin: "10px 0 0" }}>{title}</h1>
        <p className="mono small" style={{ margin: "8px 0 0", color: "var(--color-ink-2)" }}>
          {dayLabel(at)} at {clock(at)} · {zoneLabel(inc.zone_id)}
          {inc.source === "replay" ? " · replayed footage" : ""}
        </p>
      </header>

      {inc.status === "degraded" ? (
        <div style={{ marginBottom: 14 }}>
          <Banner tone="amber">
            The detailed person check did not finish for this alert. The decision
            below came from the camera alone.
          </Banner>
        </div>
      ) : null}

      {note ? (
        <div style={{ marginBottom: 14 }}>
          <Banner tone="sage">{note}</Banner>
        </div>
      ) : null}

      {/* the clip */}
      {inc.has_clip ? (
        <section style={{ marginBottom: 22 }}>
          <div
            style={{
              position: "relative",
              borderRadius: 12,
              overflow: "hidden",
              background: "#171b1f",
              aspectRatio: "16 / 9",
            }}
          >
            {clip ? (
              <video
                key={clip}
                src={clip}
                controls
                autoPlay
                muted
                playsInline
                loop
                style={{ width: "100%", height: "100%", objectFit: "contain", display: "block" }}
              />
            ) : null}
          </div>
          <div style={{ display: "flex", gap: 6, marginTop: 10, flexWrap: "wrap" }}>
            {(
              [
                ["boxes", "With the person marked"],
                ["mask", "With the room masked"],
                ["clean", "As the camera saw it"],
              ] as const
            ).map(([key, label]) => (
              <button
                key={key}
                className="btn"
                aria-pressed={view === key}
                style={{
                  minHeight: 32,
                  fontSize: 12.5,
                  padding: "0 11px",
                  background: view === key ? "var(--color-ink)" : "transparent",
                  color: view === key ? "#fbf9f5" : "var(--color-ink-2)",
                  borderColor: view === key ? "var(--color-ink)" : "var(--color-line)",
                }}
                onClick={() => setView(key)}
              >
                {label}
              </button>
            ))}
          </div>
        </section>
      ) : (
        <section
          className="card-flat"
          style={{ padding: 18, marginBottom: 22, display: "flex", gap: 12, alignItems: "center" }}
        >
          <Icon name="camera" size={18} />
          <span className="small">
            No clip was kept for this one. {inc.sam?.track_unavailable ? "The person check was unavailable, so there was nothing to annotate." : "The event was over before a clip could be cut."}
          </span>
        </section>
      )}

      {/* what happened, in steps */}
      <Timeline inc={inc} />

      {/* why */}
      <section className="card" style={{ padding: 20, marginTop: 18 }}>
        <p className="eyebrow" style={{ margin: 0 }}>Why you are getting this</p>
        <p className="lede" style={{ margin: "8px 0 0", maxWidth: 640 }}>
          {body || "Aegis noticed something in the house that matched a rule you asked to be told about."}
        </p>

        {(decision?.evidence?.length ?? 0) > 0 ? (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 14 }}>
            {(decision?.evidence ?? []).slice(0, 8).map((e, i) => (
              <span key={`${e}-${i}`} className="chip mono" style={{ textTransform: "none" }}>
                {humanFact(e)}
              </span>
            ))}
          </div>
        ) : null}

        {decision?.rationale ? (
          <blockquote
            style={{
              margin: "16px 0 0",
              paddingLeft: 14,
              borderLeft: "2px solid var(--color-line-2)",
              color: "var(--color-ink-2)",
              fontSize: 14,
              fontStyle: "italic",
              maxWidth: 620,
            }}
          >
            {decision.rationale}
          </blockquote>
        ) : null}

        <button
          className="btn btn-quiet"
          style={{ marginTop: 14, minHeight: 32, fontSize: 12.5 }}
          onClick={() => setShowRaw((v) => !v)}
          aria-expanded={showRaw}
        >
          {showRaw ? "Hide" : "How this decision was made"}
        </button>

        {showRaw ? (
          <div style={{ marginTop: 10, padding: "12px 14px", background: "var(--color-paper-2)", borderRadius: 10 }}>
            <Fact label="Model" value={decision?.model || "template"} />
            <Fact label="Prompt" value={decision?.prompt_version || "—"} />
            <Fact label="Took" value={decision?.latency_ms ? `${(decision.latency_ms / 1000).toFixed(1)}s` : "—"} />
            <Fact label="Policy floor" value={severityLabel(decision?.policy_floor || inc.policy_floor)} />
            <Fact
              label="Person check"
              value={
                inc.sam?.track_unavailable
                  ? "unavailable"
                  : `${inc.sam?.frames_present ?? 0}/${inc.sam?.frames_total ?? 0} frames`
              }
            />
            <Fact label="Phase" value={decision?.phase === 2 ? "full decision" : "first response"} />
            {(decision?.violations?.length ?? 0) > 0 ? (
              <Fact label="Corrections" value={decision?.violations?.join(", ")} />
            ) : null}
            <p className="micro" style={{ margin: "8px 0 0" }}>
              Aegis checks every decision against a fixed safety floor before it sends.
              The model may raise an alert; it can never lower one.
            </p>
          </div>
        ) : null}
      </section>

      {/* check-in */}
      <Checkin incident={inc} onDone={reload} />

      {/* what left the house */}
      <section className="card-flat" style={{ padding: 18, marginTop: 18 }}>
        <p className="eyebrow" style={{ margin: 0 }}>What left the house for this alert</p>
        <div style={{ marginTop: 8 }}>
          {inc.has_clip ? (
            <Fact
              label="Clip sent for analysis"
              value={clipBytes(inc) > 0 ? bytes(clipBytes(inc)) : "recorded in the ledger"}
            />
          ) : null}
          <Fact label="Frames analysed" value={`${inc.sam?.frames_present ?? 0}`} />
          <Fact label="Held by the model provider" value="1 hour" />
          <Fact label="Held on this machine" value="7 days" />
          <Fact label="Used for training" value="never" />
        </div>
        <Link href="/privacy" className="mono micro" style={{ display: "inline-block", marginTop: 8, textDecoration: "underline" }}>
          See every byte →
        </Link>
      </section>

      {/* actions — sticky on a phone */}
      <div
        style={{
          position: "sticky",
          bottom: 0,
          marginTop: 22,
          padding: "12px 0 calc(12px + env(safe-area-inset-bottom))",
          background: "linear-gradient(to top, var(--color-paper) 70%, transparent)",
          display: "grid",
          gridTemplateColumns: "1.2fr 1fr 1fr",
          gap: 8,
        }}
        className="max-sm:grid-cols-1"
      >
        <button
          className="btn btn-primary"
          style={{ minHeight: 48 }}
          onClick={() => act("im_checking")}
          disabled={busy !== null}
        >
          <Icon name="check" size={17} />
          {busy === "im_checking" ? "Recording…" : "I'm checking"}
        </button>
        <button className="btn" style={{ minHeight: 48 }} onClick={() => act("call_resident")} disabled={busy !== null}>
          Call her
        </button>
        <button className="btn btn-quiet" style={{ minHeight: 48 }} onClick={() => act("false_alarm")} disabled={busy !== null}>
          False alarm
        </button>
      </div>
      <p className="micro" style={{ margin: "4px 0 0" }}>
        Aegis does not call an ambulance. If she is hurt, ring one.
      </p>
    </div>
  );
}

/* ------------------------------------------------------------------ pieces */

function Timeline({ inc }: { inc: Incident }) {
  const track = (inc.track ?? {}) as Record<string, unknown>;
  const steps: { label: string; detail: string }[] = [];

  if (typeof track.dwell_in_door_zone_s === "number" && track.dwell_in_door_zone_s > 0) {
    steps.push({ label: "Approached", detail: `${Math.round(track.dwell_in_door_zone_s)}s near the door` });
  }
  if (typeof track.path_length === "number") {
    steps.push({ label: "Moved", detail: `${(track.path_length as number).toFixed(2)} of the frame` });
  }
  if (track.crossed_door_line) {
    steps.push({ label: "Crossed the door", detail: "left the watched area" });
  }
  if (inc.type.startsWith("fall")) {
    const fall = (inc.sensors ?? {}) as Record<string, unknown>;
    const noRecovery = Number(fall.no_recovery_s ?? 0);
    steps.push({ label: "Went down", detail: track.posture ? String(track.posture) : "posture changed" });
    if (noRecovery > 0) steps.push({ label: "Stayed down", detail: `${Math.round(noRecovery)}s without getting up` });
    if (fall.recovered) steps.push({ label: "Got up", detail: "movement resumed" });
  }
  if (inc.type.startsWith("stove")) {
    const sensors = (inc.sensors ?? {}) as Record<string, unknown>;
    const absent = Number(sensors.absent_s ?? 0);
    if (absent > 0) steps.push({ label: "Left the kitchen", detail: `${Math.round(absent / 60)} min ago` });
  }
  if (steps.length === 0) {
    steps.push({ label: typeLabel(inc.type), detail: zoneLabel(inc.zone_id) });
  }

  return (
    <section style={{ marginTop: 4 }}>
      <p className="eyebrow" style={{ marginBottom: 10 }}>What happened</p>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: `repeat(${Math.min(steps.length, 3)}, minmax(0, 1fr))`,
          gap: 10,
        }}
        className="max-sm:grid-cols-1"
      >
        {steps.slice(0, 3).map((s, i) => (
          <div key={`${s.label}-${i}`} className="card" style={{ padding: "12px 14px" }}>
            <p className="mono micro" style={{ margin: 0, color: "var(--color-ink-3)" }}>
              {String(i + 1).padStart(2, "0")}
            </p>
            <p className="h3" style={{ margin: "3px 0 0" }}>{s.label}</p>
            <p className="micro" style={{ margin: "2px 0 0" }}>{s.detail}</p>
          </div>
        ))}
      </div>
    </section>
  );
}

function Checkin({ incident, onDone }: { incident: Incident; onDone: () => void }) {
  const [rec, setRec] = useState<Recorder | null>(null);
  const [seconds, setSeconds] = useState(0);
  const [state, setState] = useState<"idle" | "recording" | "sending" | "done">("idle");
  const [result, setResult] = useState<CheckinResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<number | null>(null);

  // The check-in is only meaningful after an alert has actually gone out.
  const alerted = (incident.notifications ?? []).some((n) => n.status !== "failed");

  useEffect(() => () => {
    if (timer.current) window.clearInterval(timer.current);
  }, []);

  const begin = async () => {
    setError(null);
    setResult(null);
    setSeconds(0);
    try {
      const r = await startRecording(setSeconds);
      setRec(r);
      setState("recording");
      timer.current = window.setInterval(() => {
        setSeconds((s) => {
          if (s >= 30) {
            void finish();
            return s;
          }
          return s;
        });
      }, 1000);
    } catch {
      setError("The microphone is not available. Check the browser's permission for this site.");
    }
  };

  const finish = async () => {
    if (!rec) return;
    if (timer.current) window.clearInterval(timer.current);
    setState("sending");
    try {
      const wav = await rec.stop();
      setRec(null);
      const res = await fetch(`/api/incidents/${incident.id}/checkin`, {
        method: "POST",
        headers: { "content-type": "audio/wav" },
        body: wav,
      });
      const text = await res.text();
      if (!res.ok) {
        let message = `Could not send the answer (${res.status})`;
        try {
          message = (JSON.parse(text) as { detail?: string }).detail || message;
        } catch {
          /* keep the default */
        }
        throw new Error(message);
      }
      setResult(JSON.parse(text) as CheckinResult);
      setState("done");
      onDone();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not send the answer");
      setState("idle");
      setRec(null);
    }
  };

  if (!alerted) {
    return null;
  }

  return (
    <section className="card" style={{ padding: 20, marginTop: 18 }}>
      <p className="eyebrow" style={{ margin: 0 }}>Ask her</p>
      <p className="h3" style={{ margin: "6px 0 0" }}>Can she answer?</p>
      <p className="small" style={{ margin: "6px 0 0", maxWidth: 560 }}>
        Record a few seconds of her voice. Aegis transcribes it, then decides
        whether her words and what the camera sees agree. If they do not, the
        alert stands.
      </p>

      {state === "recording" ? (
        <div style={{ display: "flex", alignItems: "center", gap: 14, marginTop: 16 }}>
          <span
            className="dot dot-live"
            style={{ width: 10, height: 10, borderRadius: 999, background: "var(--color-red)", display: "inline-block" }}
            aria-hidden="true"
          />
          <span className="mono" style={{ fontSize: 20, minWidth: 54 }}>{seconds.toFixed(1)}s</span>
          <button className="btn btn-primary" style={{ minHeight: 40 }} onClick={finish}>
            Stop and send
          </button>
          <button
            className="btn btn-quiet"
            style={{ minHeight: 40 }}
            onClick={() => {
              rec?.cancel();
              if (timer.current) window.clearInterval(timer.current);
              setRec(null);
              setState("idle");
            }}
          >
            Cancel
          </button>
        </div>
      ) : (
        <button
          className="btn"
          style={{ marginTop: 14, minHeight: 44 }}
          onClick={begin}
          disabled={state === "sending" || !canRecord()}
        >
          <Icon name="live" size={17} />
          {state === "sending" ? "Transcribing…" : "Record her answer"}
        </button>
      )}

      {!canRecord() ? (
        <p className="micro" style={{ marginTop: 8 }}>
          This browser cannot record audio. The check-in works in Chrome, Edge and Safari.
        </p>
      ) : null}

      {error ? (
        <p role="alert" className="small" style={{ color: "var(--color-red)", marginTop: 12 }}>{error}</p>
      ) : null}

      {result ? <CheckinResultBlock result={result} /> : null}
    </section>
  );
}

type CheckinResult = {
  transcript: string;
  class: string;
  severity: string;
  previous: string;
  contradiction: boolean;
  resolution: string;
  reason: string;
  model: string;
};

function CheckinResultBlock({ result }: { result: CheckinResult }) {
  const tone =
    result.resolution === "escalated" || result.contradiction
      ? "red"
      : result.resolution === "self_recovered"
        ? "sage"
        : "amber";
  return (
    <div style={{ marginTop: 16 }}>
      <Banner tone={tone}>
        <strong style={{ fontWeight: 600 }}>&ldquo;{result.transcript}&rdquo;</strong>
        <br />
        {result.reason}
      </Banner>
      <div style={{ marginTop: 10 }}>
        <Fact label="Heard as" value={result.class.replace(/_/g, " ")} />
        <Fact label="Outcome" value={result.resolution.replace(/_/g, " ")} />
        <Fact label="Severity now" value={severityLabel(result.severity)} />
        <Fact label="Audio kept" value="no — deleted after transcription" />
      </div>
    </div>
  );
}

function actionCopy(action: string): string {
  switch (action) {
    case "im_checking":
      return "Recorded. Aegis will stop escalating this alert.";
    case "call_resident":
      return "Recorded that you are calling her.";
    case "false_alarm":
      return "Marked as a false alarm. The same thing will not wake you again for 30 minutes.";
    default:
      return "Recorded.";
  }
}

/** Turn a raw evidence token into the phrase a family would use. */
function humanFact(token: string): string {
  const t = token.trim();
  const colon = t.indexOf(":");
  if (colon > 0 && colon < 24) {
    const key = t.slice(0, colon).replace(/_/g, " ");
    const value = t.slice(colon + 1).trim();
    return `${key}: ${value}`;
  }
  return t.replace(/_/g, " ");
}

function clipBytes(inc: Incident): number {
  // The backend does not ship a byte count per clip in the incident payload; the
  // ledger has it. Showing an estimate here would be a small lie in a page that
  // is otherwise entirely about honesty, so it is only shown when known.
  const ledger = (inc as unknown as { ledger?: { bytes?: number }[] }).ledger;
  if (Array.isArray(ledger)) {
    return ledger.filter((r) => r.bytes).reduce((sum, r) => sum + (r.bytes ?? 0), 0);
  }
  return 0;
}
