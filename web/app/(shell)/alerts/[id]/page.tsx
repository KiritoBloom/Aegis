"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { Icon } from "@/components/icons";
import {
  Banner,
  Fact,
  SeverityChip,
  Skeleton,
} from "@/components/ui";
import {
  bytes,
  clock,
  dayLabel,
  severityLabel,
  typeLabel,
  zoneLabel,
  type CheckinRecord,
  type ContextRead,
  type Incident,
} from "@/lib/api";
import { useApi, useStream } from "@/lib/useApi";
import { canRecord, startRecording, type Recorder } from "@/lib/recorder";

/** One alert in reading order: danger, evidence, clip, and the next action. */
export default function AlertDetail() {
  const params = useParams<{ id: string }>();
  const id = params?.id ?? "";
  const { data: inc, error, loading, reload } = useApi<Incident>(`/api/incidents/${id}`, 8000);
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [view, setView] = useState<"clean" | "boxes" | "mask">("boxes");
  const [showRaw, setShowRaw] = useState(false);

  useStream((frame) => {
    if (frame === "snapshot" || frame.startsWith("incident.") || frame.startsWith("checkin.") ||
        frame === "context.read") {
      reload();
    }
  });

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
        <Link href="/events" className="mono micro" style={{ textDecoration: "underline" }}>
          ← Events
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
      <Link href="/events" className="mono micro" style={{ textDecoration: "underline" }}>
        ← Events
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

      {/* The check-in is the thing happening right now. A night-presence alert
          never speaks in the room: a voice from the device could make that
          situation worse. */}
      {inc.type === "unexpected_presence" ? (
        <section className="card-flat" style={{ padding: "14px 18px", marginTop: 18 }}>
          <p className="eyebrow" style={{ margin: 0 }}>No spoken prompt</p>
          <p className="small" style={{ margin: "5px 0 0" }}>
            Aegis does not ask a question out loud for this alert. If a second person is in the
            house, a voice from the device could make the situation worse. The family gets the
            clip and calls her themselves.
          </p>
        </section>
      ) : (
        <Checkin incident={inc} onDone={reload} />
      )}

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

      <ObjectsChecked inc={inc} />

      <WhatChecked inc={inc} />

      <ModelJobs inc={inc} />

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
            <Fact label="Phase" value={decision?.phase === 3 ? "check-in escalation" : decision?.phase === 2 ? "full decision" : "first response"} />
            {(decision?.violations?.length ?? 0) > 0 ? (
              <Fact label="Corrections" value={decision?.violations?.join(", ")} />
            ) : null}
            {(inc.agent?.length ?? 0) > 0 ? (
              <div style={{ marginTop: 14, paddingTop: 12, borderTop: "1px solid var(--color-line)" }}>
                <p className="eyebrow" style={{ margin: 0 }}>Evidence trail</p>
                <ol style={{ margin: "8px 0 0", padding: 0, listStyle: "none", display: "grid", gap: 7 }}>
                  {(inc.agent ?? []).map((step, i) => (
                    <li key={`${step.step}-${step.tool ?? i}`} style={{ display: "grid", gridTemplateColumns: "96px minmax(0,1fr)", gap: 8 }}>
                      <span className="mono micro" style={{ color: "var(--color-ink-3)", overflow: "hidden", textOverflow: "ellipsis" }}>
                        {step.tool ? `${step.step} · ${step.tool}` : step.step}
                      </span>
                      <span style={{ fontSize: 11.5, lineHeight: 1.5, color: "var(--color-ink-2)" }}>
                        {step.summary || "recorded"}
                      </span>
                    </li>
                  ))}
                </ol>
              </div>
            ) : null}
            <p className="micro" style={{ margin: "8px 0 0" }}>
              Aegis checks every decision against a fixed safety floor before it sends.
              The model may raise an alert; it can never lower one.
            </p>
          </div>
        ) : null}
      </section>

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
  if (inc.type === "unexpected_presence") {
    const sensors = (inc.sensors ?? {}) as Record<string, unknown>;
    const people = Number(track.person_count ?? sensors.person_count ?? 0);
    steps.push({ label: "After dark", detail: "inside the 22:00–06:00 window" });
    steps.push({ label: "Second person", detail: `${Math.max(people, 2)} people tracked` });
    steps.push({
      label: "The door",
      detail: String(sensors.door_contact ?? "unknown") === "open" ? "open" : "state unknown",
    });
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
  const [clock, setClock] = useState(() => Date.now());
  const timer = useRef<number | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  // The check-in is only meaningful after an alert has gone out, or while the
  // spoken check-in is pending / has produced a record of its own.
  const alerted = (incident.notifications ?? []).some((n) => n.status !== "failed");
  const status = incident.checkin_status ?? null;
  const pending = status === "asking";
  const timedOut = status === "no_answer";
  const answered = status === "answered";
  const cancelled = status === "cancelled";
  const latest = incident.checkin ?? null;
  const visible = alerted || Boolean(status);

  useEffect(() => {
    if (!pending) return;
    const t = window.setInterval(() => setClock(Date.now()), 500);
    return () => window.clearInterval(t);
  }, [pending]);

  useEffect(() => () => {
    if (timer.current) window.clearInterval(timer.current);
  }, []);

  if (!visible) {
    return null;
  }

  const secondsLeft =
    pending && incident.checkin_due_at
      ? Math.max(0, Math.ceil(incident.checkin_due_at - clock / 1000))
      : null;
  const promptText = incident.checkin_prompt || "Are you okay? Say I'm okay, or say help.";

  const speak = () => {
    if (typeof window === "undefined" || !("speechSynthesis" in window)) return;
    const utterance = new SpeechSynthesisUtterance(promptText);
    utterance.rate = 0.92;
    window.speechSynthesis.cancel();
    window.speechSynthesis.speak(utterance);
  };

  const begin = async () => {
    setError(null);
    setResult(null);
    setSeconds(0);
    try {
      audioRef.current?.pause();
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

  return (
    <section className="card" style={{ padding: 20, marginTop: 18, borderColor: pending ? "var(--color-amber)" : undefined }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "start", flexWrap: "wrap" }}>
        <div>
          <p className="eyebrow" style={{ margin: 0 }}>
            {pending ? "Aegis is asking her now" : "Check-in"}
          </p>
          <p className="h3" style={{ margin: "6px 0 0" }}>
            {pending
              ? "Waiting for an answer"
              : timedOut
                ? "No answer"
                : answered
                  ? "She answered"
                  : cancelled
                    ? "Check-in paused"
                    : "Can she answer?"}
          </p>
        </div>
        {pending && secondsLeft !== null ? (
          <span className="chip chip-amber mono" style={{ fontSize: 11 }}>
            escalates in {secondsLeft}s
          </span>
        ) : null}
      </div>

      {pending || timedOut || answered ? (
        <>
          <blockquote
            style={{
              margin: "14px 0 0",
              padding: "11px 14px",
              borderLeft: "2px solid var(--color-amber)",
              background: "var(--color-paper-2)",
              borderRadius: "0 8px 8px 0",
              fontSize: 15,
              color: "var(--color-ink)",
            }}
          >
            &ldquo;{promptText}&rdquo;
          </blockquote>
          {incident.checkin_audio_url ? (
            <audio
              ref={audioRef}
              src={incident.checkin_audio_url}
              controls
              autoPlay
              style={{ width: "100%", marginTop: 10, height: 38 }}
            />
          ) : (
            <button className="btn" type="button" onClick={speak} style={{ marginTop: 10, minHeight: 36, fontSize: 13 }}>
              Speak the question
            </button>
          )}
          {pending ? (
            <Link
              href={`/checkin/${incident.id}`}
              target="_blank"
              rel="noreferrer"
              className="mono micro"
              style={{ display: "inline-block", marginTop: 8, textDecoration: "underline" }}
            >
              Open the resident screen ↗
            </Link>
          ) : null}
        </>
      ) : null}

      {timedOut ? (
        <div style={{ marginTop: 14 }}>
          <Banner tone="red">
            No answer within {Math.round(incident.checkin_timeout_s ?? 15)} seconds. The alert was
            escalated one step automatically. If this was a mistake, call her.
          </Banner>
          {latest ? <CheckinRecordBlock record={latest} /> : null}
        </div>
      ) : null}

      {answered && latest ? (
        <div style={{ marginTop: 14 }}>
          <Banner tone={latest.outcome === "self_recovered" ? "sage" : "amber"}>
            <strong style={{ fontWeight: 600 }}>&ldquo;{latest.transcript}&rdquo;</strong>
            <br />
            {latest.read?.read || latest.outcome.replace(/_/g, " ")}
          </Banner>
          <CheckinRecordBlock record={latest} />
        </div>
      ) : null}

      {!answered && !cancelled ? (
        <>
          <p className="small" style={{ margin: "10px 0 0", maxWidth: 640 }}>
            {pending
              ? "The question is being spoken in the room. Record her answer now, or let the deadline escalate on its own."
              : "Record a few seconds of her voice. Aegis transcribes it, then decides whether her words and what the camera sees agree. If they do not, the alert stands."}
          </p>

          {state === "recording" ? (
            <div style={{ display: "flex", alignItems: "center", gap: 14, marginTop: 16, flexWrap: "wrap" }}>
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
              {state === "sending"
                ? "Muse is reading her answer…"
                : "Record her answer"}
            </button>
          )}

          {!canRecord() ? (
            <p className="micro" style={{ marginTop: 8 }}>
              This browser cannot record audio. The check-in works in Chrome, Edge and Safari.
            </p>
          ) : null}
        </>
      ) : null}

      {error ? (
        <p role="alert" className="small" style={{ color: "var(--color-red)", marginTop: 12 }}>{error}</p>
      ) : null}

      {result && !answered ? <CheckinResultBlock result={result} /> : null}
    </section>
  );
}

function CheckinRecordBlock({ record }: { record: CheckinRecord }) {
  return (
    <div style={{ marginTop: 12 }}>
      <Fact label="Heard as" value={record.read?.class?.replace(/_/g, " ") || record.outcome.replace(/_/g, " ")} />
      <Fact label="Outcome" value={record.outcome.replace(/_/g, " ")} />
      <Fact label="Read by" value={record.model} />
      {record.read ? (
        <p className="small" style={{ margin: "8px 0 0", color: "var(--color-ink-2)" }}>
          <strong style={{ fontWeight: 600 }}>Muse read:</strong> {record.read.read}
        </p>
      ) : null}
    </div>
  );
}

function WhatChecked({ inc }: { inc: Incident }) {
  const sensors = (inc.sensors ?? {}) as Record<string, unknown>;
  const audio = (sensors.audio ?? {}) as Record<string, unknown>;
  const media = (sensors.media ?? {}) as Record<string, unknown>;
  const track = (inc.track ?? {}) as Record<string, unknown>;
  const context: ContextRead | null = inc.context_read ?? null;
  const isVoice = inc.type === "help_call" || Boolean(audio.distress) || Boolean(context);
  const isPresence = inc.type === "unexpected_presence";
  if (!isVoice && !isPresence) return null;

  const transcript = String(audio.transcript ?? "");
  const kind = String(audio.kind ?? "");
  const personCount = Number(track.person_count ?? sensors.person_count ?? 0);
  const posture = String(track.posture ?? sensors.posture ?? "unknown");
  const tv = mediaState(media.tv_on);
  const radio = mediaState(media.radio_on);
  const heard = transcript || (kind === "scream_like" ? "A loud cry, no words" : "No words detected");
  const doorContact = String(sensors.door_contact ?? "unknown");

  if (isPresence) {
    return (
      <section className="card" style={{ padding: 20, marginTop: 18 }}>
        <p className="eyebrow" style={{ margin: 0 }}>What I checked</p>
        <p className="small" style={{ margin: "6px 0 0", maxWidth: 620 }}>
          A second body after dark is the signal. Aegis does not know who the person is.
        </p>
        <div style={{ display: "grid", gap: 10, marginTop: 14 }} className="sm:grid-cols-2">
          <div className="card-flat" style={{ padding: "12px 14px" }}>
            <p className="eyebrow" style={{ margin: 0 }}>People</p>
            <p className="small" style={{ margin: "5px 0 8px", color: "var(--color-ink)" }}>
              {Math.max(personCount, 2)} tracked
            </p>
            <Fact label="Resident" value={personCount > 0 ? "in frame" : "not seen"} />
            <Fact label="Identity" value="never checked" />
          </div>
          <div className="card-flat" style={{ padding: "12px 14px" }}>
            <p className="eyebrow" style={{ margin: 0 }}>The room</p>
            <Fact label="Door" value={doorContact} />
            <Fact label="Posture" value={posture.replace(/_/g, " ")} />
            <Fact label="Second person" value="after dark" />
          </div>
        </div>
        <p className="micro" style={{ margin: "12px 0 0" }}>
          Aegis does not identify people and does not dispatch anyone. If someone is in danger,
          call emergency services.
        </p>
      </section>
    );
  }

  return (
    <section className="card" style={{ padding: 20, marginTop: 18 }}>
      <p className="eyebrow" style={{ margin: 0 }}>What I checked</p>
      <p className="small" style={{ margin: "6px 0 0", maxWidth: 620 }}>
        A cry is only actionable after the room has been checked. These are the feeds
        that went into this decision.
      </p>
      <div style={{ display: "grid", gap: 10, marginTop: 14 }} className="sm:grid-cols-2">
        <div className="card-flat" style={{ padding: "12px 14px" }}>
          <p className="eyebrow" style={{ margin: 0 }}>Audio</p>
          <p className="small" style={{ margin: "5px 0 8px", color: "var(--color-ink)" }}>
            {heard}
          </p>
          <Fact label="Signal" value={kind.replace(/_/g, " ") || "—"} />
          <Fact
            label="Level"
            value={typeof audio.rms_dbfs === "number" ? `${audio.rms_dbfs} dBFS` : "—"}
          />
        </div>
        <div className="card-flat" style={{ padding: "12px 14px" }}>
          <p className="eyebrow" style={{ margin: 0 }}>Room</p>
          <Fact label="Person" value={personCount > 0 ? "in frame" : "not seen"} />
          <Fact label="Posture" value={posture.replace(/_/g, " ")} />
          <Fact label="Television" value={tv} />
          <Fact label="Radio" value={radio} />
        </div>
      </div>
      {context ? (
        <div style={{ marginTop: 12, padding: "12px 14px", borderRadius: 10, background: "var(--color-paper-2)" }}>
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <span className="eyebrow" style={{ margin: 0 }}>Muse read the room</span>
            <span className="chip mono" style={{ textTransform: "none" }}>{context.source}</span>
            <span className="chip mono" style={{ textTransform: "none" }}>{severityLabel(context.severity)}</span>
          </div>
          <p className="small" style={{ margin: "6px 0 0", color: "var(--color-ink-2)" }}>
            {context.read}
          </p>
          {context.evidence.length ? (
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 8 }}>
              {context.evidence.map((e, i) => (
                <span key={`${e}-${i}`} className="chip mono" style={{ textTransform: "none" }}>{humanFact(e)}</span>
              ))}
            </div>
          ) : null}
        </div>
      ) : null}
      <p className="micro" style={{ margin: "12px 0 0" }}>
        Aegis does not diagnose a medical cause. If she is in danger, call emergency services.
      </p>
    </section>
  );
}

function mediaState(value: unknown): string {
  if (value === true) return "on";
  if (value === false) return "off";
  return "not checked";
}

function ObjectsChecked({ inc }: { inc: Incident }) {
  const sensors = (inc.sensors ?? {}) as Record<string, unknown>;
  const raw = Array.isArray(sensors.objects) ? sensors.objects : [];
  const objects = raw
    .filter((o): o is { label: string; conf: number; zone?: string | null } =>
      Boolean(o && typeof o === "object" && "label" in o))
    .slice(0, 10);
  const support = sensors.support_surface as { label?: string; conf?: number } | null;
  if (!objects.length) return null;

  return (
    <section className="object-context" style={{ marginTop: 18 }} aria-label="Objects in the incident">
      <div className="object-context-head">
        <span className="eyebrow">Objects in the frame</span>
        <span className="mono micro">
          {sensors.objects_at ? `seen ${clock(String(sensors.objects_at))}` : "sampled by the edge"}
        </span>
      </div>
      <div className="object-chips">
        {objects.map((obj, i) => (
          <span key={`${obj.label}-${i}`} className="object-chip">
            <strong>{obj.label}</strong>
            <span className="mono">{Math.round((obj.conf ?? 0) * 100)}%</span>
            {obj.zone ? <span className="object-zone">{zoneLabel(obj.zone)}</span> : null}
          </span>
        ))}
      </div>
      {support?.label ? (
        <p className="micro" style={{ margin: "9px 0 0" }}>
          <span className="eyebrow" style={{ marginRight: 7 }}>Support surface</span>
          {support.label}
          {support.conf ? ` · ${Math.round(support.conf * 100)}%` : ""}
        </p>
      ) : null}
      <p className="micro" style={{ margin: "8px 0 0" }}>
        Object labels are room context, not identity. They help explain a posture; they do not
        replace the person check.
      </p>
    </section>
  );
}

function ModelJobs({ inc }: { inc: Incident }) {
  const decision = inc.decision;
  const hasAudio = inc.type === "help_call" || Boolean(inc.checkin) || Boolean(inc.checkin_status);
  const rows: { label: string; value: string }[] = [];
  if (hasAudio) {
    rows.push({ label: "Heard by", value: "Muse Voice Transcribe" });
  }
  if (inc.has_clip) {
    rows.push({ label: "Person geometry", value: "SAM 3.1" });
  }
  if (decision?.model?.startsWith("muse-")) {
    rows.push({ label: "Family wording", value: "Muse Spark" });
  } else if (decision) {
    rows.push({ label: "Family wording", value: "deterministic template" });
  }
  if (inc.context_read) {
    rows.push({ label: "Ambiguous room", value: "Muse Spark" });
  }
  if (inc.checkin?.read) {
    rows.push({ label: "Her answer read by", value: "Muse Spark" });
  }
  if (!rows.length) return null;
  return (
    <section className="card-flat" style={{ padding: "14px 18px", marginTop: 18 }}>
      <p className="eyebrow" style={{ margin: 0 }}>Models on this alert</p>
      <div style={{ marginTop: 6 }}>
        {rows.map((row) => (
          <Fact key={row.label} label={row.label} value={row.value} />
        ))}
      </div>
      <p className="micro" style={{ margin: "6px 0 0" }}>
        Raw pixels never reach the language model. SAM reads the clip; Muse reads the structured
        evidence; the check-in question is spoken by the house itself so it works offline.
      </p>
    </section>
  );
}

type CheckinResult = {
  transcript: string;
  class: string;
  classifier_class?: string;
  severity: string;
  previous: string;
  contradiction: boolean;
  resolution: string;
  reason: string;
  read?: { class: string; read: string; evidence: string[]; confidence: number } | null;
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
        {result.read?.read || result.reason}
      </Banner>
      <div style={{ marginTop: 10 }}>
        <Fact label="Heard as" value={result.class.replace(/_/g, " ")} />
        <Fact label="Outcome" value={result.resolution.replace(/_/g, " ")} />
        <Fact label="Severity now" value={severityLabel(result.severity)} />
        <Fact label="Read by" value={result.model} />
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
