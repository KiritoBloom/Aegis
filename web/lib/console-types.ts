export type Person = {
  box: number[];
  display_box?: number[];
  skeleton?: number[][];
  velocity?: number[];
  track_state?: "acquiring" | "confirmed";
  conf: number;
  posture: string;
  support_surface?: string | null;
  track_id?: number;
  primary?: boolean;
  track_hits?: number;
  track_age_s?: number;
  distance?: "near" | "mid" | "far";
};

export type WallFrame = {
  feed_id: string;
  feed_name: string;
  at: string;
  width: number;
  height: number;
  person_count: number;
  persons: Person[];
  objects: { label: string; conf: number; box: number[] }[];
  flags: string[];
  status: string;
  paused?: boolean;
  tracking: { fps?: number; samples?: number; lying_for_s?: number; processing_ms?: number;
    inference_ms?: number; queue_ms?: number; round_trip_ms?: number; media_delay_ms?: number;
    reused_frame?: boolean; rechecks?: number; confirmed_tracks?: number };
  alert: WallAlert | null;
  last_alert?: WallAlert | null;
  resolved: WallAlert | null;
};

export type WallAlert = {
  id: string;
  kind: "possible_fall" | "multiple_people";
  incident_id?: string;
  severity?: "alert" | "warn";
  active: boolean;
  evidence?: { lying_for_s?: number; support_surface?: string | null; person_count?: number };
  verification?: {
    status?: string;
    stage?: string;
    reason?: string;
    escalated?: boolean;
    started_at?: number;
    updated_at?: string;
    clip_seconds?: number;
    sam_person_count?: number;
    sam_seen?: boolean;
    confidence?: number;
    timeline?: boolean;
  };
  recovered?: boolean;
};

export type FeedSource = { url?: string; stream?: MediaStream; error?: string };
export type LiveFeed = {
  frame?: WallFrame;
  observation?: { image: HTMLImageElement; mediaTime: number; ended: boolean };
  status: "idle" | "watching" | "ended" | "error";
  error?: string;
  receivedAt?: number;
};
export type LogEntry = {
  id: number;
  at: number;
  source: "Vision" | "Alert" | "Model" | "System";
  tone: "info" | "good" | "warn" | "alert";
  message: string;
  detail?: string;
  pipelineId?: number;
};
export type PipelineStep = {
  id: number; incident_id: string; feed_id: string; feed_name: string;
  stage: string; status: "running" | "done" | "failed"; title: string;
  detail: string; created_at: string;
  metrics: { duration_ms?: number; elapsed_ms?: number; tool?: string; question?: string;
    tools?: string[]; verdict?: string; severity?: string; person_count?: number;
    frames_present?: number; frames_total?: number; vertical_drop?: number;
    height_ratio?: number; aspect_start?: number; aspect_end?: number; median_conf?: number;
    trajectory?: number[][]; start_box?: number[]; end_box?: number[]; primary_track_id?: number;
    due_at?: number; timeout_s?: number; audio_url?: string; resident_url?: string;
    channels?: string[]; resolution?: string; clip_seconds?: number; lying_for_s?: number;
    track_id?: number; box?: number[];
    phase?: string; follow_up?: boolean; model?: string; destination?: string; bytes?: number;
    request?: Record<string, unknown>; response?: unknown; media?: InspectionMedia[] };
};
export type InspectionMedia = {
  name: string; kind: "video" | "image" | "text"; label: string; description: string;
  privacy: "raw" | "segmented" | "blurred" | "data"; bytes: number; url: string;
};
export type ConsoleToast = { id: number; tone: "info" | "good" | "warn" | "alert"; title: string; detail?: string; incidentId?: string };
