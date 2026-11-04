import type { InspectionMedia, PipelineStep } from "./console-types";

export const INSPECTION_STAGES = [
  { id: "local", label: "Local vision", note: "Notice a change" },
  { id: "clip", label: "Event clip", note: "Collect the evidence" },
  { id: "sam", label: "SAM", note: "Segment & track" },
  { id: "privacy", label: "Privacy", note: "Blur locally" },
  { id: "muse", label: "Muse", note: "Review & decide" },
  { id: "checkin", label: "Response", note: "Ask the resident first" },
] as const;
export type InspectionStage = typeof INSPECTION_STAGES[number]["id"];
export type InspectionJob = PipelineStep & { updated_at: string };

export function inspectionStage(stage: string): InspectionStage {
  return INSPECTION_STAGES.some((item) => item.id === stage) ? stage as InspectionStage : "checkin";
}

/** Local preview jobs can finish after Muse starts; following live must not jump backwards. */
export function inspectionLiveStage(rows: PipelineStep[]): InspectionStage {
  const index = rows.reduce((latest, row) => Math.max(latest,
    INSPECTION_STAGES.findIndex((item) => item.id === inspectionStage(row.stage))), 0);
  return INSPECTION_STAGES[index].id;
}

/** Keep start/request + completion/response together, including concurrent tools. */
export function inspectionJobs(rows: PipelineStep[]): InspectionJob[] {
  const jobs = new Map<string, InspectionJob>();
  for (const row of rows) {
    const key = JSON.stringify([row.stage, row.stage === "checkin" ? "resident_checkin" : row.metrics.phase || row.title, row.metrics.tool,
      row.metrics.question, row.metrics.follow_up || false]);
    const prior = jobs.get(key);
    const present = Object.fromEntries(Object.entries(row.metrics).filter(([, value]) => value != null));
    jobs.set(key, { ...row, id: prior?.id ?? row.id, created_at: prior?.created_at ?? row.created_at,
      updated_at: row.created_at, metrics: { ...prior?.metrics, ...present } });
  }
  return [...jobs.values()].sort((a, b) => a.id - b.id);
}

export function inspectionMedia(saved: InspectionMedia[], rows: PipelineStep[]): InspectionMedia[] {
  return [...new Map([...saved, ...rows.flatMap((row) => row.metrics.media ?? [])]
    .map((media) => [media.name, media])).values()];
}

/** A completed investigation is not a promise that an older/missing video will arrive. */
export function inspectionVideoState(name: "annotated.mp4" | "privacy.mp4", media: InspectionMedia[],
  rows: PipelineStep[], load: "loading" | "error" | "ready" = "ready"): string {
  if (media.some((item) => item.name === name)) return "Ready to watch ↗";
  if (load === "error") return "Connection error";
  if (load === "loading") return "Loading";
  const stage = name === "annotated.mp4" ? "sam" : "privacy";
  const phase = name === "annotated.mp4" ? "render" : "video";
  const job = rows.findLast((row) => row.stage === stage && row.metrics.phase === phase);
  if (job) return job.status === "running" ? "Processing" : "Unavailable";
  if (rows.some((row) => row.stage === "sam" && row.status === "failed")) return "Unavailable";
  if (rows.some((row) => row.stage === "muse" || inspectionStage(row.stage) === "checkin")) return "Not recorded";
  return "Waiting";
}

export function record(value: unknown): Record<string, unknown> {
  return value != null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown> : {};
}
