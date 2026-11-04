"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import { useConsole } from "./console-provider";
import { LiveFeed } from "./live-feed";
import { MuseComparison } from "./muse-comparison";
import { Icon } from "./icons";
import { useApi } from "@/lib/useApi";
import { INSPECTION_STAGES, inspectionJobs, inspectionMedia, inspectionStage, inspectionLiveStage, inspectionVideoState, record,
  type InspectionJob, type InspectionStage } from "@/lib/inspection";
import type { InspectionMedia, PipelineStep } from "@/lib/console-types";
import styles from "./deeper-look.module.css";

const clock = (iso: string) => new Date(iso).toLocaleTimeString("en-GB", { hour12: false });
const size = (bytes: number) => bytes >= 1048576 ? `${(bytes / 1048576).toFixed(1)} MB` : `${Math.ceil(bytes / 1024)} KB`;
const MEDIA_NAMES: Record<InspectionStage, string[]> = {
  local: [], clip: ["clip.mp4", "still.jpg"], sam: ["annotated.mp4", "sam_input.mp4", "clip.mp4"],
  privacy: ["privacy.mp4", "privacy.jpg", "redacted.mp4", "redacted.jpg"],
  muse: ["privacy.jpg", "redacted.jpg"], checkin: [],
};
const WAITING: Record<InspectionStage, string> = {
  local: "Start monitoring to see the live local observation.",
  clip: "The event recording appears here as soon as the evidence window is collected.",
  sam: "The video sent to SAM appears first. Playable segmentation appears as soon as its returned masks are rendered.",
  privacy: "The protected image and blurred video appear here as soon as local processing finishes.",
  muse: "The safety review can inspect a protected still. The demo comparison above shows the independent image-sequence and hex-only requests.",
  checkin: "The resident's question and the recorded outcome appear here when the evidence review finishes.",
};

export function DeeperLook({ feedId }: { feedId: string }) {
  const { ready, feeds, pipeline, live, active, switching, setActive, pipelineError } = useConsole();
  const [selectedEvent, setSelectedEvent] = useState("");
  const feed = feeds.find((item) => item.id === feedId);
  const feedRows = pipeline.filter((row) => row.feed_id === feedId);
  const events = [...new Set(feedRows.map((row) => row.incident_id))].reverse();
  const alert = live[feedId]?.frame?.alert ?? live[feedId]?.frame?.last_alert;
  if (alert?.active && alert.incident_id && !events.includes(alert.incident_id)) events.unshift(alert.incident_id);
  const eventId = selectedEvent || events[0];

  if (!ready) return <p className={styles.notice}>Opening the live walkthrough…</p>;
  if (!feed) return <div className={styles.page}><Link href="/" className={styles.back}>← Back to cameras</Link>
    <h1>Camera not found</h1><p>This camera is no longer configured in this browser.</p></div>;

  return <div className={styles.page}>
    <Link href={`/?feed=${encodeURIComponent(feedId)}`} className={styles.back}>← Back to camera view</Link>
    <header className={styles.header}>
      <div><h1>A deeper look<span>{feed.name || feed.label}</span></h1>
        <p>Follow the evidence from camera to decision. Real inputs, real outputs, as they arrive.</p></div>
      <span className={styles.liveBadge} data-live={active}><span />{active ? "Live walkthrough" : "Saved walkthrough"}</span>
    </header>
    <div className={styles.toolbar}>
      <label>Investigation<select value={selectedEvent} onChange={(event) => setSelectedEvent(event.target.value)}>
        <option value="">Latest investigation{events[0] ? ` · ${clock(feedRows.find((row) => row.incident_id === events[0])?.created_at ?? new Date().toISOString())}` : " · waiting for an event"}</option>
        {events.map((id) => <option value={id} key={id}>{clock(feedRows.find((row) => row.incident_id === id)?.created_at ?? new Date().toISOString())} · {id.slice(-8)}</option>)}
      </select></label>
      <p><Icon name="privacy" size={16} />Private demo evidence. Two independent Muse reads compare protected images with text-only motion.</p>
    </div>
    {!active ? <div className={styles.notice}>Monitoring is paused. You can explore saved steps, or start a new live walkthrough.
      <button className={styles.button} disabled={switching} onClick={() => void setActive(true)}>{switching ? "Starting…" : "Start monitoring"}</button></div> : null}
    {pipelineError ? <p className={styles.notice}>Saved diagnostics could not load. Live updates will still appear when connected.</p> : null}
    {eventId ? <EventWalkthrough key={eventId} feedId={feedId} eventId={eventId} followLatest={!selectedEvent} />
      : <Walkthrough feedId={feedId} rows={[]} media={[]} followLatest loading={false} />}
  </div>;
}

function EventWalkthrough({ feedId, eventId, followLatest }: { feedId: string; eventId: string; followLatest: boolean }) {
  const { pipeline } = useConsole();
  const saved = useApi<{ items: PipelineStep[]; media: InspectionMedia[] }>(`/api/diagnostics/${encodeURIComponent(eventId)}`, 15000);
  const rows = useMemo(() => [...new Map([...(saved.data?.items ?? []), ...pipeline.filter((row) => row.incident_id === eventId)]
    .map((row) => [row.id, row])).values()].sort((a, b) => a.id - b.id), [saved.data, pipeline, eventId]);
  return <>
    {saved.error ? <div className={styles.notice} role="alert">The evidence connection failed. Videos cannot be loaded until it reconnects; recorded progress alone does not mean playback is available.
      <button className={styles.button} onClick={saved.reload}>Retry</button></div> : null}
    <Walkthrough feedId={feedId} rows={rows} media={inspectionMedia(saved.data?.media ?? [], rows)} followLatest={followLatest}
      loading={saved.loading && !saved.data} evidenceError={Boolean(saved.error)} />
  </>;
}

function Walkthrough({ feedId, rows, media, followLatest, loading, evidenceError = false }: {
  feedId: string; rows: PipelineStep[]; media: InspectionMedia[]; followLatest: boolean; loading: boolean; evidenceError?: boolean;
}) {
  const { feeds, sources, live, active, logs } = useConsole();
  const feed = feeds.find((item) => item.id === feedId)!;
  const state = live[feedId];
  const jobs = useMemo(() => inspectionJobs(rows), [rows]);
  const [following, setFollowing] = useState(followLatest);
  const [pickedStage, setPickedStage] = useState<InspectionStage>(inspectionStage(jobs.at(-1)?.stage ?? "local"));
  const [pickedJob, setPickedJob] = useState<number | null>(null);
  const [pickedMedia, setPickedMedia] = useState("");
  const activityRef = useRef<HTMLOListElement>(null);
  const stage = following ? inspectionLiveStage(rows) : pickedStage;
  const stageJobs = jobs.filter((job) => inspectionStage(job.stage) === stage);
  const selectedJob = jobs.find((job) => job.id === pickedJob)
    ?? stageJobs.findLast((job) => job.metrics.request || job.metrics.response) ?? stageJobs.at(-1);
  const candidates = MEDIA_NAMES[stage].flatMap((name) => media.find((item) => item.name === name) ?? []);
  const artifact = (!following && candidates.find((item) => item.name === pickedMedia)) || candidates[0];
  const sam = rows.findLast((row) => row.stage === "sam" && row.metrics.frames_present != null);
  const decision = jobs.findLast((job) => job.metrics.phase === "decision" && job.status === "done");
  const verdict = record(decision?.metrics.response);
  const ask = rows.findLast((row) => row.stage === "checkin" && row.metrics.resident_url);
  const outcome = jobs.findLast((job) => ["delivery", "response", "recovery", "recovered", "rejected", "stopped", "paused", "acknowledged"].includes(job.stage)
    || job.metrics.verdict === "rejected" || job.metrics.resolution === "self_recovered");
  const imageRequest = artifact && rows.findLast((row) => row.metrics.phase === "image" && row.metrics.media?.some((item) => item.name === artifact.name));
  const stageInfo = INSPECTION_STAGES.find((item) => item.id === stage)!;
  const loadState = evidenceError ? "error" : loading ? "loading" : "ready";
  const videoState = stage === "sam" || stage === "privacy"
    ? inspectionVideoState(stage === "sam" ? "annotated.mp4" : "privacy.mp4", media, rows, loadState) : null;
  const missingEvidence = evidenceError ? "The evidence connection failed. Retry above to load this investigation."
    : videoState === "Not recorded" ? "This investigation has no saved video for this step. Earlier investigations may predate video inspection; start a new recording to capture the full walkthrough."
      : videoState === "Unavailable" ? "The video was not produced or is no longer available. Recorded measurements remain below."
        : null;

  useEffect(() => {
    if (following && activityRef.current) activityRef.current.scrollTop = activityRef.current.scrollHeight;
  }, [following, rows.length]);

  const explore = (value: InspectionStage, jobId: number | null = null) => {
    setFollowing(false); setPickedStage(value); setPickedJob(jobId); setPickedMedia("");
  };

  return <>
    <nav className={styles.stages} aria-label="Evidence pipeline">
      {INSPECTION_STAGES.map((item, index) => {
        const latest = rows.findLast((row) => inspectionStage(row.stage) === item.id);
        const status = item.id === "local" && !rows.length ? active ? "Observing locally" : "Paused"
          : latest ? latest.status === "running" ? "In progress"
            : latest.status === "failed" ? "Unavailable" : "Recorded" : "Waiting";
        return <button key={item.id} type="button" onClick={() => explore(item.id)} aria-pressed={stage === item.id}
          className={styles.stage} data-selected={stage === item.id} data-status={status}>
          <span className={styles.stageNumber}>{String(index + 1).padStart(2, "0")}</span>
          <strong>{item.label}</strong><span>{item.note}</span><small>{status}</small>
        </button>;
      })}
    </nav>
    <MuseComparison rows={rows} />
    <div className={styles.layout}>
      <section className={styles.evidence} aria-label="Evidence viewer">
        <header className={styles.panelHead}><div><h2>{stageInfo.label}</h2><p>{stageInfo.note}</p></div>
          <button type="button" className={styles.follow} data-on={following} aria-pressed={following}
            onClick={() => { setFollowing(!following); setPickedStage(stage); setPickedJob(null); setPickedMedia(""); }}>
            <span />{following ? "Following live" : "Follow live"}
          </button></header>
        {candidates.length ? <div className={styles.mediaTabs} aria-label="Available evidence">
          {candidates.map((item) => <button key={item.name} type="button" aria-pressed={artifact?.name === item.name}
            onClick={() => { setFollowing(false); setPickedStage(stage); setPickedMedia(item.name); setPickedJob(null); }}>{item.label}</button>)}
        </div> : null}
        {artifact ? <ArtifactPreview key={artifact.url} artifact={artifact} />
          : stage === "local" || stage === "checkin" ? <div className={styles.camera}>
            {sources[feedId] && !sources[feedId].error && state?.observation
              ? <LiveFeed feed={feed} source={sources[feedId]} frame={state?.frame} active={active} detailed />
              : <div className={styles.waiting}><Icon name="camera" size={28} /><h3>{state?.error || sources[feedId]?.error ? "Camera observation interrupted" : "Waiting for a camera observation"}</h3>
                <p>{state?.error || sources[feedId]?.error || WAITING.local}</p></div>}
          </div> : <div className={styles.waiting} data-loading={loading}><Icon name={stage === "privacy" ? "privacy" : "live"} size={28} />
            <h3>{missingEvidence ? "Evidence unavailable" : stageJobs.at(-1)?.status === "failed" ? "This step could not finish" : loading ? "Loading saved evidence" : "Evidence appears here when ready"}</h3>
            <p>{missingEvidence ?? (stageJobs.at(-1)?.status === "failed" ? stageJobs.at(-1)?.detail : WAITING[stage])}</p></div>}
        <div className={styles.caption}>
          <span>{artifact ? stage === "muse" ? imageRequest ? imageRequest.status === "done" ? "Image sent to Muse" : imageRequest.status === "failed" ? "Image request failed" : "Sending this image to Muse" : "Available to Muse · not sent yet"
            : artifact.privacy === "raw" ? "Private evidence" : artifact.privacy === "blurred" ? "Blurred locally" : "Rendered from SAM masks"
            : stage !== "local" && stage !== "checkin" ? `${stageInfo.label} · ${missingEvidence ? "evidence unavailable" : "awaiting evidence"}`
              : state?.status === "ended" ? "Local vision · final recorded frame" : "Local vision · latest analyzed frame"}</span>
          <p>{artifact?.description ?? (stage === "local" ? "The image and pose below come from the same local observation. Continuous frames are not sent to Muse."
            : stage === "checkin" ? "Resident check-in and family response are separate from the model's recommendation." : missingEvidence ?? WAITING[stage])}</p>
          {artifact ? <a href={artifact.url} target="_blank" rel="noreferrer">Open evidence ↗ <span>{size(artifact.bytes)}</span></a> : null}
        </div>
        <div className={styles.readyEvidence} aria-label="Pipeline video availability">
          {[{ name: "annotated.mp4" as const, stage: "sam" as const, label: "SAM segmentation" },
            { name: "privacy.mp4" as const, stage: "privacy" as const, label: "Blurred video" }].map((item) => {
            const available = media.some((entry) => entry.name === item.name);
            return <button type="button" key={item.name} data-ready={available} onClick={() => {
              explore(item.stage); setPickedMedia(item.name);
            }}><Icon name="live" size={16} /><span>{item.label}</span><small>{inspectionVideoState(item.name, media, rows, loadState)}</small></button>;
          })}
        </div>
        {stage === "sam" && sam ? <dl className={styles.facts}>
          <div><dt>People tracked</dt><dd>{sam.metrics.person_count ?? "—"}</dd></div>
          <div><dt>Frames with the person</dt><dd>{sam.metrics.frames_present ?? "—"} / {sam.metrics.frames_total ?? "—"}</dd></div>
          <div><dt>Downward image movement</dt><dd>{sam.metrics.vertical_drop != null ? `${Math.round(sam.metrics.vertical_drop * 100)}%` : "Not measured"}</dd></div>
        </dl> : null}
        {stage === "muse" && decision ? <div className={styles.decision}>
          <span>Muse recommendation</span><h3>{verdict.verdict === "reject" ? "Dismiss the candidate" : verdict.verdict === "escalate" ? "Continue the safety response" : "Evidence reviewed"}</h3>
          <p>{String(verdict.rationale ?? decision.detail)}</p>
          <small>This recommendation passes through the local safety policy and resident-first check-in.</small>
        </div> : null}
        {stage === "checkin" && ask ? <div className={styles.resident}><span>Resident check-in</span><blockquote>“{ask.detail}”</blockquote>
          {ask.metrics.audio_url ? <audio controls preload="none" src={ask.metrics.audio_url} aria-label="Resident check-in prompt" /> : null}
          <a href={ask.metrics.resident_url} target="_blank" rel="noreferrer" className={styles.button}>Open resident check-in ↗</a>
          {outcome ? <p><strong>{outcome.title}</strong><br />{outcome.detail}</p> : <p>Waiting for the resident's answer. No outcome is assumed.</p>}
        </div> : null}
        {selectedJob ? <JobDetails key={selectedJob.id} job={selectedJob} rawMask={stage === "sam" ? media.find((item) => item.kind === "text") : undefined} />
          : <div className={styles.explainer}><h3>{stage === "local" ? "The camera stays local until an event needs review." : "Waiting for this step"}</h3>
            <p>{stage === "local" ? "Local pose and tracking identify a candidate. Then SAM measures the event, Muse reviews the evidence, and Aegis asks the resident before alerting family." : WAITING[stage]}</p></div>}
      </section>
      <aside className={styles.activity} aria-label="Live pipeline activity">
        <header className={styles.panelHead}><div><h2>What’s happening</h2><p>{jobs.length ? `${jobs.length} recorded steps` : "Waiting for an event"}</p></div><span className={styles.dot} data-live={active} /></header>
        <p className={styles.activityHint}>Select a step to inspect its evidence. Follow live to return to the newest stage.</p>
        <ol ref={activityRef} className={styles.activityList}>
          {jobs.map((job) => {
            const running = job.status === "running";
            return <li key={job.id}><button type="button" data-selected={selectedJob?.id === job.id}
              onClick={() => explore(inspectionStage(job.stage), job.id)}>
              <span className={styles.jobMark} data-state={job.status === "failed" ? "failed" : running ? "running" : "done"}>{job.status === "failed" ? "!" : running ? "·" : <Icon name="check" size={13} />}</span>
              <span className={styles.jobBody}><span className={styles.jobMeta}>{job.metrics.model ?? INSPECTION_STAGES.find((item) => item.id === inspectionStage(job.stage))?.label}<time>{clock(job.updated_at)}</time></span>
                <strong>{job.title.replace(/^SAM · |^Muse · /, "")}</strong><span>{job.detail}</span>
                {job.metrics.duration_ms != null ? <small>{(job.metrics.duration_ms / 1000).toFixed(1)}s</small> : null}
              </span>
            </button></li>;
          })}
          {!jobs.length ? <li className={styles.emptyActivity}><Icon name="shield" size={22} /><h3>No event needs review yet</h3><p>Live camera observations stay local. The pipeline opens when an event is detected.</p>
            {(logs[feedId] ?? []).filter((entry) => !entry.pipelineId).slice(-3).map((entry) => <div key={entry.id}><strong>{entry.message}</strong><span>{entry.detail}</span></div>)}</li> : null}
        </ol>
        <footer className={styles.activityFoot}>Recorded actions and returned decisions only. No simulated progress or hidden model reasoning.</footer>
      </aside>
    </div>
  </>;
}

function ArtifactPreview({ artifact }: { artifact: InspectionMedia }) {
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const src = attempt ? `${artifact.url}?retry=${attempt}` : artifact.url;
  if (failed) return <div className={styles.waiting}><Icon name="live" size={28} /><h3>Evidence could not be played</h3>
    <p>The artifact may have expired or the connection was interrupted.</p>
    <button className={styles.button} onClick={() => { setFailed(false); setAttempt(attempt + 1); }}>Retry playback</button></div>;
  return <div className={styles.preview}>{artifact.kind === "video"
    ? <video key={src} src={src} controls autoPlay muted loop playsInline preload="metadata" onError={() => setFailed(true)} aria-label={artifact.label} />
    // Exact runtime evidence, not an optimized/resized replacement image.
    // eslint-disable-next-line @next/next/no-img-element
    : <img src={src} alt={artifact.label} onError={() => setFailed(true)} />}</div>;
}

function JobDetails({ job, rawMask }: { job: InspectionJob; rawMask?: InspectionMedia }) {
  return <section className={styles.details} aria-label="Selected step details">
    <div className={styles.detailHeading}><h3>{job.title}</h3><span>{job.status === "failed" ? "Failed" : job.status === "running" ? "Awaiting completion" : "Returned"}</span></div>
    <p>{job.detail}</p>
    {job.metrics.question ? <blockquote>{job.metrics.question}</blockquote> : null}
    {job.metrics.destination ? <p className={styles.destination}>{job.metrics.destination === "local" ? "Read locally" : `Destination: ${job.metrics.destination}`}{job.metrics.model ? ` · ${job.metrics.model}` : ""}{job.metrics.bytes != null ? ` · ${size(job.metrics.bytes)}` : ""}</p> : null}
    {job.metrics.request ? <details><summary>What was sent</summary><pre>{JSON.stringify(job.metrics.request, null, 2)}</pre></details> : null}
    {job.metrics.response != null ? <details><summary>{job.metrics.destination === "local" ? "Evidence returned by the tool" : "Response received"}</summary><pre>{JSON.stringify(job.metrics.response, null, 2)}</pre></details> : null}
    {!job.metrics.request && job.metrics.response == null ? <details><summary>Recorded measurements</summary><pre>{JSON.stringify(Object.fromEntries(Object.entries(job.metrics).filter(([key]) => key !== "media")), null, 2)}</pre></details> : null}
    {rawMask ? <a className={styles.rawLink} href={rawMask.url} target="_blank" rel="noreferrer">View original SAM mask response ↗</a> : null}
  </section>;
}
