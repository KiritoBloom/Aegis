"use client";

import { useState } from "react";
import { inspectionJobs, record } from "@/lib/inspection";
import type { PipelineStep } from "@/lib/console-types";
import styles from "./muse-comparison.module.css";

const labels: Record<string, string> = { fall: "Possible fall", controlled_lowering: "Controlled lowering",
  getting_up: "Getting up", walking: "Walking", exercise: "Exercise", uncertain: "Not enough evidence" };

/** Both views use the same persisted requests and live results, never demo verdicts. */
export function MuseComparison({ rows, compact = false }: { rows: PipelineStep[]; compact?: boolean }) {
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const jobs = inspectionJobs(rows);
  const pair = ["image", "hex"].map((mode) => jobs.findLast((job) => job.metrics.phase === `compare_${mode}`));
  const ready = rows.some((row) => row.metrics.media?.some((media) => media.name === "sam_response.txt"));
  const samFailed = rows.some((row) => row.stage === "sam" && row.status === "failed") && !ready;
  const complete = pair.every((job) => job?.status === "done");
  const agrees = complete && record(pair[0]?.metrics.response).action === record(pair[1]?.metrics.response).action;
  const id = rows[0]?.incident_id;

  async function retry() {
    setSending(true); setError("");
    try {
      const response = await fetch(`/api/diagnostics/${encodeURIComponent(id!)}/compare`, { method: "POST" });
      if (!response.ok) { const body = await response.json(); throw new Error(body.detail || "Could not start the comparison"); }
    } catch (err) { setError(err instanceof Error ? err.message : "Could not start the comparison"); }
    finally { setSending(false); }
  }

  return <section className={styles.comparison} data-compact={compact} aria-label="Image and hexadecimal comparison">
    <header className={styles.header}><div><span>Meta Muse · two independent reads</span><h3>One event. Images or just numbers.</h3></div>
      <span className={styles.badge}>Demo</span></header>
    <p className={styles.intro}>The image review sees a protected sequence. The hex review receives only SAM silhouettes encoded as text.</p>
    <div className={styles.pair}>{["image", "hex"].map((mode, index) => {
      const job = pair[index]; const answer = record(job?.metrics.response); const request = job?.metrics.request;
      const preview = job?.metrics.media?.find((media) => media.kind === "image");
      return <article className={styles.read} key={mode} data-state={job?.status ?? "waiting"}>
        <div className={styles.readHead}><strong>{mode === "image" ? "Image review" : "Hex-only review"}</strong>
          <span>{job?.status === "done" ? "Returned" : job?.status === "failed" || samFailed ? "Unavailable" : job ? "Reviewing…" : ready ? "Ready to compare" : "Awaiting SAM"}</span></div>
        <p className={styles.input}>{mode === "image" ? "Blurred people + room context" : "32 × 18 bits · zero images, videos or tools"}</p>
        <h4>{job?.status === "done" ? labels[String(answer.action)] ?? "Review returned" : job?.status === "failed" ? "Review could not finish" : samFailed ? "SAM evidence unavailable" : "Waiting for a real result"}</h4>
        {job ? <p className={styles.observation}>{job.detail}</p> : null}
        {job?.status === "done" ? <p className={styles.stats}>{typeof answer.confidence === "number" ? `Model confidence ${Math.round(answer.confidence * 100)}%` : ""}
          {job.metrics.duration_ms != null ? ` · ${(job.metrics.duration_ms / 1000).toFixed(1)}s` : ""}</p> : null}
        {Array.isArray(answer.evidence_frames) && answer.evidence_frames.length ? <p className={styles.stats}>Cited frames: {answer.evidence_frames.join(", ")}</p> : null}
        {request ? <details className={styles.inputs}><summary>{mode === "image" ? "See the images sent" : "See the exact hex input"}</summary>
          {preview ? <a href={preview.url} target="_blank" rel="noreferrer">
            {/* Exact local evidence; the hex preview is decoded locally, not sent as pixels. */}
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={preview.url} alt={mode === "image" ? "Exact protected contact sheet sent to Muse" : "Local rendering of the hex bitmaps"} loading="lazy" /></a> : null}
          {mode === "hex" ? <><p>{String(request.binary_occupancy_bytes ?? "—")} bytes of occupancy · {String(request.json_input_bytes ?? "—")} bytes of JSON input.</p>
            <pre>{JSON.stringify(request.packet, null, 2)}</pre></> : null}
          <details><summary>Request instructions and schema</summary><pre>{JSON.stringify(request, null, 2)}</pre></details>
          {job?.status === "done" ? <details><summary>Full model response</summary><pre>{JSON.stringify(answer, null, 2)}</pre></details> : null}
        </details> : null}
      </article>;
    })}</div>
    <footer className={styles.footer}><p>{complete ? agrees ? "Both reads agree on the observed action." : "The reads disagree. Room context and movement can tell different stories."
      : "Results appear independently as each model call finishes."} These demo observations do not change the resident-first alert decision.</p>
      {id && !complete && ready ? <button type="button" disabled={sending} onClick={() => void retry()}>{sending ? "Starting…" : pair.some(Boolean) ? "Retry unfinished reads" : "Run comparison"}</button> : null}</footer>
    {error ? <p className={styles.error} role="alert">{error}</p> : null}
  </section>;
}
