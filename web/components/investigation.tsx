"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { useNow } from "@/lib/useApi";
import type { PipelineStep } from "@/lib/console-types";
import styles from "./investigation.module.css";

const STAGES = ["local", "clip", "sam", "muse", "checkin", "outcome"];
const LABELS = ["Noticed", "Video", "Motion", "Review", "Check-in", "Outcome"];
const OUTCOMES = new Set(["delivery", "response", "recovery", "recovered", "rejected", "paused", "stopped", "acknowledged"]);
const seconds = (ms: number) => `${(ms / 1000).toFixed(1)}s`;
const clock = (value: string) => new Date(value).toLocaleTimeString([], { hour12: false });
const toolLabels: Record<string, string> = { household_memory: "Household information", recent_events: "Recent activity", audio_context: "Available audio", inspect_frame: "Room image" };
function activityTitle(row: PipelineStep) {
  if (row.stage === "local") return "Unusual movement noticed";
  if (row.stage === "clip") return row.status === "running" ? "Collecting a short recording" : "Recording ready to review";
  if (row.stage === "sam") return row.status === "failed" ? "Movement check could not finish" : row.status === "running" ? "Following the person's movement" : "Movement checked";
  if (row.stage === "muse") {
    if (row.metrics.tool) return `${toolLabels[row.metrics.tool] || "Evidence"} ${row.status === "running" ? "being checked" : "checked"}`;
    if (row.metrics.verdict === "verified") return "The resident will be asked if they need help";
    if (row.metrics.verdict === "rejected") return "No fall confirmed";
    return row.status === "running" ? "Reviewing the evidence" : "Evidence review updated";
  }
  return row.title;
}

export function Investigation({ steps, error }: { steps: PipelineStep[]; error?: string | null }) {
  const now = useNow(1000);
  const [selected, setSelected] = useState("");
  const incidents = useMemo(() => [...new Set(steps.map((step) => step.incident_id))].reverse(), [steps]);
  const id = incidents.includes(selected) ? selected : incidents[0];
  const rows = steps.filter((step) => step.incident_id === id);
  const last = rows.at(-1);
  const sam = rows.findLast((row) => row.stage === "sam" && row.status === "done");
  const verdict = rows.findLast((row) => ["verified", "rejected", "unavailable", "recovered"].includes(row.metrics.verdict ?? ""));
  const ask = rows.findLast((row) => row.stage === "checkin");
  const prompt = rows.findLast((row) => row.metrics.resident_url);
  const outcome = rows.findLast((row) => OUTCOMES.has(row.stage) || row.metrics.resolution === "self_recovered" || row.metrics.verdict === "rejected");
  const waiting = ask?.status === "running" && !outcome && ask.metrics.due_at != null;
  const remaining = waiting ? Math.max(0, Math.min(ask.metrics.timeout_s ?? Infinity, Math.ceil(ask.metrics.due_at! - now / 1000))) : 0;
  const activeStage = outcome ? "outcome" : waiting ? "checkin" : last?.stage;
  const complete = outcome || verdict?.metrics.verdict === "rejected";
  const total = rows.length ? Math.max(0, (complete ? new Date(last!.created_at).getTime() : now) - new Date(rows[0].created_at).getTime()) : 0;
  const lower = (sam?.metrics.vertical_drop ?? 0) >= .15;
  const flatter = (sam?.metrics.height_ratio ?? 1) < .65;
  const checked = verdict?.metrics.verdict === "rejected";
  const stageMessage = outcome ? "The result of this check is recorded below."
    : waiting ? "Aegis is asking the resident whether they need help."
    : activeStage === "muse" ? "Aegis is checking the room and the movement before deciding what to do."
    : activeStage === "sam" ? "Aegis is following the person through a short recording."
    : "Aegis noticed a change and is taking a closer look.";

  return <section className={styles.investigation} aria-label="Live investigation">
    <header className={styles.panelHead}>
      <div><p className={styles.eyebrow}>Checking on your household</p><h2>{outcome?.stage === "response" ? "Family responding" : outcome?.stage === "recovery" || outcome?.metrics.resolution === "self_recovered" ? "Recovery confirmed" : checked ? "No fall confirmed" : outcome?.stage === "delivery" ? "An alert needs attention" : waiting ? "Are you okay?" : rows.length ? "Taking a closer look" : "Ready when needed"}</h2></div>
      {rows.length ? <span className={styles.elapsed} title="Time since this check began">{Math.floor(total / 1000)}s</span> : null}
    </header>
    {incidents.length > 1 ? <label className={styles.incidentSelect}>Investigation<select value={id} onChange={(e) => setSelected(e.target.value)}>
      {incidents.map((value) => { const first = steps.find((row) => row.incident_id === value)!;
        return <option value={value} key={value}>{clock(first.created_at)} · {first.feed_name} · {value.slice(-6)}</option>; })}
    </select></label> : null}
    {error ? <p className={styles.notice}>Saved diagnostics could not load. Live steps will still appear when the stream is connected.</p> : null}
    <ol className={styles.stages} aria-label="Investigation stages">
      {STAGES.map((stage, index) => {
        const matching = rows.filter((row) => stage === "outcome" ? OUTCOMES.has(row.stage) || row.metrics.resolution === "self_recovered" || row.metrics.verdict === "rejected" : row.stage === stage);
        const latest = matching.at(-1);
        const state = activeStage === stage && !complete ? "active" : latest ? latest.status === "failed" ? "failed" : "seen" : "waiting";
        return <li key={stage} data-state={state}><span>{String(index + 1).padStart(2, "0")}</span>{LABELS[index]}<small>{state === "active" ? "Now" : state === "seen" ? "Checked" : state === "failed" ? "Unavailable" : "Waiting"}</small></li>;
      })}
    </ol>
    {!rows.length ? <div className={styles.empty}>
      <p>Aegis stays local until something needs a closer look.</p>
      <dl><div><dt>Follow the person</dt><dd>Local pose, motion and room context identify a candidate.</dd></div>
        <div><dt>Investigate the evidence</dt><dd>SAM measures motion. Muse chooses which household records and protected images to inspect.</dd></div>
        <div><dt>Ask before interrupting</dt><dd>A resident check-in can resolve an event before the family is alerted.</dd></div></dl>
    </div> : <>
      <p className={styles.stageMessage} role="status">{stageMessage}</p>
      {waiting && prompt ? <div className={styles.resident}>
        <div className={styles.residentHead}><span>Resident check-in</span><strong>{remaining ? `${remaining}s to answer` : "Awaiting timeout result"}</strong></div>
        <blockquote>“{prompt.detail}”</blockquote>
        {prompt.metrics.audio_url ? <audio controls preload="none" src={prompt.metrics.audio_url} aria-label="Play the resident prompt" /> : null}
        <a href={prompt.metrics.resident_url} target="_blank" rel="noreferrer" className={styles.residentLink}>Answer the check-in <span aria-hidden="true">↗</span></a>
        <small>Opens in a new tab so camera tracking keeps running.</small>
      </div> : null}
      {outcome ? <div className={styles.outcome} data-alert={outcome.stage === "delivery"}><span>{outcome.title}</span><p>{outcome.detail || "The outcome has been recorded."}</p>
        {["delivery", "response"].includes(outcome.stage) ? <Link href={`/alerts/${encodeURIComponent(id)}`}>Open the family alert →</Link> : null}
      </div> : null}
      {sam ? <section className={styles.observationSummary} aria-label="What Aegis noticed">
        <p className={styles.eyebrow}>What the recording shows</p>
        <h3>{lower || flatter ? "A change in body position" : "The person's movement was reviewed"}</h3>
        <p>{lower && flatter ? "The person moved lower in the picture and their outline became flatter."
          : lower ? "The person moved lower in the camera view."
            : flatter ? "The person's outline became flatter during the recording."
              : "Aegis followed the person's position through the recording."} This alone does not tell us whether they fell.</p>
        <div className={styles.observationFact}><span aria-hidden="true">✓</span><span>{sam.metrics.frames_present && sam.metrics.frames_total ? `Person followed in ${sam.metrics.frames_present} of ${sam.metrics.frames_total} video frames` : "Movement reviewed from the event recording"}</span></div>
        <details className={styles.measurements}>
          <summary>How the movement was checked</summary>
          <p>Meta SAM follows the person across the recording. Meta Muse reviews that evidence alongside the room context.</p>
          <dl><div><dt>Downward movement in the picture</dt><dd>{sam.metrics.vertical_drop != null ? `${Math.round(sam.metrics.vertical_drop * 100)}% of image height` : "Not measured"}</dd></div>
            <div><dt>Height of the person's outline at the end</dt><dd>{sam.metrics.height_ratio != null ? `${Math.round(sam.metrics.height_ratio * 100)}% of its starting height` : "Not measured"}</dd></div></dl>
          <p>These describe the camera image, not the person's actual height or distance fallen.</p>
        </details>
      </section> : null}
      <div className={styles.timelineHead}><h3>This check, step by step</h3><span>{rows.length} updates</span></div>
      <ol className={styles.timeline}>
        {rows.map((row, index) => {
          const isRunning = row.status === "running" && !complete && !rows.slice(index + 1).some((next) => next.stage === row.stage && next.metrics.tool === row.metrics.tool) && activeStage === row.stage;
          return <li key={row.id} data-state={isRunning ? "running" : row.status === "failed" ? "failed" : "done"}>
            <div className={styles.stepMark}>{isRunning ? <span className={styles.pulse} /> : String(index + 1).padStart(2, "0")}</div>
            <div className={styles.stepBody}><div className={styles.stepTitle}><strong>{activityTitle(row)}</strong><span>{row.metrics.duration_ms != null ? seconds(row.metrics.duration_ms) : clock(row.created_at)}</span></div>
              <p>{row.detail}</p>{row.metrics.question && row.metrics.question !== row.detail ? <blockquote>{row.metrics.question}</blockquote> : null}
              {row.metrics.tools?.length ? <div className={styles.tools}>{row.metrics.tools.map((tool) => <span key={tool}>{toolLabels[tool] || tool.replaceAll("_", " ")}</span>)}</div> : null}
            </div>
          </li>;
        })}
      </ol>
    </>}
    <footer className={styles.footnote}>Aegis checks the evidence and asks the resident before deciding whether to alert the family.</footer>
  </section>;
}
