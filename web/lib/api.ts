/**
 * The client-side data layer.
 *
 * Everything here is a plain fetch with a poll, on purpose. A care dashboard
 * that depends on a websocket staying open is a dashboard that is stale at the
 * exact moment the network is busy — which is when an alert is happening. SSE is
 * used as an *upgrade* for the incident list (it makes the page feel alive), but
 * every view still renders correctly from a single GET.
 */

export type Severity = "info" | "watch" | "urgent" | "emergency";

export type Zone = {
  name: string;
  type: string;
  points: [number, number][];
  private?: boolean;
  rest?: boolean;
  schedule?: string | null;
  risk?: string;
};

export type Incident = {
  id: string;
  type: string;
  status: string;
  severity: Severity;
  title?: string | null;
  body?: string | null;
  zone_id?: string | null;
  local_time?: string | null;
  created_at?: string | null;
  started_at?: string | null;
  source?: string;
  severity_hint?: string;
  policy_floor?: Severity;
  notify_now?: number | boolean;
  notify_phase?: number;
  has_clip?: boolean;
  clip_url?: string;
  annotated_url?: string;
  still_url?: string;
  thumb_url?: string;
  evidence?: string[];
  decision?: Decision | null;
  sam?: SamResult | null;
  track?: Record<string, unknown> | null;
  sensors?: Record<string, unknown> | null;
  notifications?: Notification[];
  acks?: Ack[];
};

export type Decision = {
  title?: string;
  body?: string;
  severity?: Severity;
  evidence?: string[];
  actions?: string[];
  rationale?: string;
  confidence?: number;
  follow_up_s?: number;
  model?: string;
  prompt_version?: string;
  latency_ms?: number;
  violations?: string[];
  policy_floor?: Severity;
  phase?: number;
};

export type SamResult = {
  frames_total?: number;
  frames_present?: number;
  person_count?: number;
  track_unavailable?: boolean;
  error?: string;
};

export type Notification = {
  channel: string;
  phase: number;
  status: string;
  error?: string | null;
  created_at?: string;
};

export type Ack = { action: string; created_at: string };

export type WatchClass = {
  id: string;
  label: string;
  question: string;
  detail: string;
  tier: "safety" | "care" | "comfort";
  default: string;
  locked: boolean;
  modes: string[];
  examples: Record<string, string>;
  types: string[];
};

export type WatchPlan = {
  modes: Record<string, string>;
  door_hours: "night" | "always";
  quiet: { enabled: boolean; start: number; end: number };
  zones: Record<string, boolean>;
  onboarded_at: string | null;
  consent_at: string | null;
  consent_by: string;
};

export type WatchState = {
  plan: WatchPlan;
  summary: {
    alert: string[];
    digest: string[];
    off: string[];
    locked: string[];
    door_hours: string;
    quiet: { enabled: boolean; start: number; end: number };
    zones_off?: string[];
  };
  classes: WatchClass[];
  zones: Zone[];
  quiet: WatchPlan["quiet"];
  door_hours: string;
  onboarded_at: string | null;
  consent_at: string | null;
};

export type Stats = {
  events_seen?: number;
  alerts_sent?: number;
  stayed_home?: number;
  restraint_rate?: number;
  restraint?: {
    seen: number;
    seen_window: number;
    alerts: number;
    stayed_home: number;
    window_days: number;
    by_zone: { name: string; count: number }[];
    by_type: { name: string; count: number }[];
  };
  heartbeat?: Record<string, unknown> | null;
  ledger?: LedgerTotals;
  failed_notifications?: number;
  latency_ms?: { p50: number; p95: number; mean: number; max: number; n: number };
  degraded_decisions?: number;
  acks?: number;
  outbound?: { bytes: number; rows: number };
};

export type LedgerTotals = {
  total_bytes: number;
  rows: number;
  idle_bytes: number;
  video_bytes: number;
  audio_rows: number;
  audio_bytes: number;
};

export type LedgerRow = {
  id: number;
  incident_id: string | null;
  destination: string;
  purpose: string;
  bytes: number;
  frames: number;
  retention: string;
  contains_video: number;
  contains_audio: number;
  created_at: string;
  latency_ms: number;
  model: string | null;
};

export type AegisEvent = {
  id?: number;
  incident_id?: string | null;
  camera_id?: string | null;
  type: string;
  zone_id?: string | null;
  ts: string;
  severity: Severity;
  suppressed: number;
  reason?: string | null;
  source?: string;
};

export type Household = {
  household: { id: string; name: string; timezone: string };
  profile: {
    display_name: string;
    age: number;
    cognitive_condition: string;
    wanders: boolean;
    uses_mobility_aid: boolean;
    anticoagulant: boolean;
    lives_alone: boolean;
  };
  zones: Zone[];
  privacy: { consent: ConsentState; pause: PauseState };
  timezone: string;
  watch: WatchState["summary"];
  onboarded: boolean;
};

export type ConsentState = {
  consented: boolean;
  member?: string;
  version?: string;
  given_at?: string | null;
  text?: string;
};

export type PauseState = {
  paused: boolean;
  by?: string;
  reason?: string;
  at?: string | null;
};

export type Camera = {
  camera: {
    id: string;
    online: boolean;
    fps: number | null;
    frames: number | null;
    detector: string | null;
    queue_depth: number | null;
    last_seen: string | null;
  };
  zones: Zone[];
  still: { url: string; incident_id: string; at: string; type: string; zone: string } | null;
  masks: string[];
};

const BASE = "";

export async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { accept: "application/json" },
    cache: "no-store",
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`${res.status} ${res.statusText}${text ? ` — ${text.slice(0, 200)}` : ""}`);
  }
  return (await res.json()) as T;
}

export async function send<T>(
  path: string,
  body: unknown,
  method: "POST" | "PUT" | "DELETE" = "POST",
): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method,
    headers: { "content-type": "application/json" },
    body: method === "DELETE" ? undefined : JSON.stringify(body ?? {}),
  });
  const text = await res.text();
  if (!res.ok) {
    let message = `${res.status} ${res.statusText}`;
    try {
      const parsed = JSON.parse(text) as { detail?: string };
      if (parsed.detail) message = parsed.detail;
    } catch {
      if (text) message = text.slice(0, 240);
    }
    throw new Error(message);
  }
  return (text ? JSON.parse(text) : {}) as T;
}

/** Bytes as a person would say them. "0 B" is a claim, so it stays exact. */
export function bytes(n: number | null | undefined): string {
  const v = Number(n ?? 0);
  if (v === 0) return "0 B";
  if (v < 1024) return `${v} B`;
  if (v < 1024 * 1024) return `${(v / 1024).toFixed(v < 10240 ? 1 : 0)} KB`;
  return `${(v / (1024 * 1024)).toFixed(1)} MB`;
}

const TYPE_LABEL: Record<string, string> = {
  door_open: "Front door opened",
  door_approach: "Someone at the door",
  door_left_open: "Door left open",
  fall_suspected: "Possible fall",
  fall_confirmed: "Fall",
  fall_assisted: "Fall, someone helping",
  stove_unattended: "Stove left on",
  person_unwell_still: "No movement for a long time",
  camera_offline: "Camera offline",
  device_offline: "Device offline",
  checkin_no_response: "No answer to a check-in",
  motion_only: "Movement",
  pet_motion: "Pet moving",
  prayer_activity: "Prayer",
  normal_rest: "Resting",
};

export function typeLabel(t: string | null | undefined): string {
  if (!t) return "Something happened";
  return TYPE_LABEL[t] ?? t.replace(/_/g, " ");
}

export function severityLabel(s: Severity | string | null | undefined): string {
  switch (s) {
    case "emergency":
      return "Emergency";
    case "urgent":
      return "Urgent";
    case "watch":
      return "Worth a look";
    default:
      return "Noted";
  }
}

export function severityTone(s: Severity | string | null | undefined): string {
  switch (s) {
    case "emergency":
    case "urgent":
      return "red";
    case "watch":
      return "amber";
    default:
      return "sage";
  }
}

/** 3:12 AM — the form used on a phone at 3 AM. */
export function clock(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d
    .toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", hour12: false })
    .replace(/^0/, "");
}

export function dayLabel(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const today = new Date();
  const same = d.toDateString() === today.toDateString();
  if (same) return "Today";
  const y = new Date(today);
  y.setDate(y.getDate() - 1);
  if (d.toDateString() === y.toDateString()) return "Yesterday";
  return d.toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" });
}

export function ago(iso: string | null | undefined): string {
  if (!iso) return "never";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "never";
  const s = Math.max(0, Math.round((Date.now() - t) / 1000));
  if (s < 45) return "just now";
  if (s < 90) return "a minute ago";
  const m = Math.round(s / 60);
  if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h} hour${h === 1 ? "" : "s"} ago`;
  const d = Math.round(h / 24);
  return `${d} day${d === 1 ? "" : "s"} ago`;
}

export function hourLabel(h: number): string {
  const hour = ((h % 24) + 24) % 24;
  const suffix = hour < 12 ? "am" : "pm";
  const twelve = hour % 12 === 0 ? 12 : hour % 12;
  return `${twelve}${suffix}`;
}

export function zoneLabel(name: string | null | undefined): string {
  if (!name) return "unknown area";
  return name.replace(/_/g, " ");
}
