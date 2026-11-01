export type Person = {
  box: number[];
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
  tracking: { fps?: number; samples?: number; lying_for_s?: number };
  alert: WallAlert | null;
  last_alert?: WallAlert | null;
  resolved: WallAlert | null;
};

export type WallAlert = {
  id: string;
  kind: "possible_fall" | "multiple_people";
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
  };
  recovered?: boolean;
};

export type FeedSource = { url?: string; stream?: MediaStream; error?: string };
export type LiveFeed = {
  frame?: WallFrame;
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
};
export type ConsoleToast = { id: number; tone: "info" | "good" | "warn" | "alert"; title: string; detail?: string };
