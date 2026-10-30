"use client";

export type FeedKind = "file" | "demo" | "camera";

export type ConsoleFeed = {
  id: string;
  label: string;
  name: string;
  location: string;
  kind: FeedKind;
  replay: boolean;
  demoUrl?: string;
  deviceId?: string;
  addedAt: string;
};

export type Occupant = {
  id: string;
  name: string;
  age: number | null;
  relationship: string;
  conditions: string[];
  livesAlone: boolean;
};

export type SetupProposal = {
  summary: string;
  notes: string[];
  modes: Record<string, string>;
  model: string;
  at: string;
};

export type FeedMapping = {
  feedId: string;
  at: string;
  width: number;
  height: number;
  personCount: number;
  objects: string[];
  flags: string[];
  status: string;
  local: true;
};

export type ConsoleSetup = {
  version: 1;
  completed: boolean;
  feeds: ConsoleFeed[];
  occupants: Occupant[];
  caregiver: string;
  caregiverPhone: string;
  watchModes: Record<string, string>;
  quiet: { enabled: boolean; start: number; end: number };
  doorHours: "night" | "always";
  proposal: SetupProposal | null;
  museConsent: boolean;
  configApproved: boolean;
  configApprovedAt: string | null;
  mapping: Record<string, FeedMapping>;
  consentName: string;
  consentAt: string | null;
  createdAt: string;
};

const STORAGE_KEY = "aegis.console.v1";
const MEDIA_DB = "aegis-console-media";
const MEDIA_STORE = "media";

export const MAX_CONSOLE_FEEDS = 6;

export const CONDITION_SUGGESTIONS = [
  { id: "dementia", label: "Dementia", cognitive: "dementia" },
  { id: "alzheimers", label: "Alzheimer's", cognitive: "alzheimers" },
  { id: "depression", label: "Depression", cognitive: null },
  { id: "low_mood", label: "Low mood", cognitive: null },
  { id: "mobility", label: "Mobility support", cognitive: null },
  { id: "hearing", label: "Hearing difficulty", cognitive: null },
  { id: "vision", label: "Vision difficulty", cognitive: null },
  { id: "blood_thinner", label: "Blood thinner", cognitive: null },
] as const;

export const ROOM_SUGGESTIONS = [
  "Living room",
  "Bedroom",
  "Hallway",
  "Kitchen",
  "Front door",
  "Bathroom door",
];

export function newId(prefix = "feed"): string {
  const cryptoObj = globalThis.crypto as Crypto | undefined;
  if (cryptoObj?.randomUUID) return `${prefix}-${cryptoObj.randomUUID().slice(0, 8)}`;
  return `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`;
}

export function newOccupant(relationship = "Resident"): Occupant {
  return {
    id: newId("person"),
    name: "",
    age: null,
    relationship,
    conditions: [],
    livesAlone: true,
  };
}

export function defaultSetup(): ConsoleSetup {
  return {
    version: 1,
    completed: false,
    feeds: [],
    occupants: [newOccupant()],
    caregiver: "",
    caregiverPhone: "",
    watchModes: {},
    quiet: { enabled: true, start: 22, end: 7 },
    doorHours: "night",
    proposal: null,
    museConsent: false,
    configApproved: false,
    configApprovedAt: null,
    mapping: {},
    consentName: "",
    consentAt: null,
    createdAt: new Date().toISOString(),
  };
}

export function loadSetup(): ConsoleSetup | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as ConsoleSetup & { resident?: Occupant };
    if (!parsed || parsed.version !== 1) return null;
    const occupants = parsed.occupants?.length ? parsed.occupants : parsed.resident ? [parsed.resident] : [newOccupant()];
    return {
      ...defaultSetup(),
      ...parsed,
      occupants: occupants.map((person) => ({ ...newOccupant(person.relationship), ...person })),
    };
  } catch {
    return null;
  }
}

export function saveSetup(setup: ConsoleSetup): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(setup));
}

export function primaryOccupant(setup: ConsoleSetup): Occupant {
  return setup.occupants[0] ?? newOccupant();
}

export function setupReadyToWatch(setup: ConsoleSetup | null): { ready: boolean; reason: string | null } {
  if (!setup || !setup.completed) return { ready: false, reason: "Finish the setup first." };
  if (!setup.feeds.length) return { ready: false, reason: "Add at least one camera." };
  const unmapped = setup.feeds.filter((f) => !setup.mapping[f.id]);
  if (unmapped.length) {
    return {
      ready: false,
      reason: `Map ${unmapped.map((f) => f.name || f.label).join(", ")} before Aegis can watch.`,
    };
  }
  if (!setup.configApproved) return { ready: false, reason: "Approve the configuration Muse prepared before activation." };
  return { ready: true, reason: null };
}

export function profilePayload(occupant: Occupant, livesAlone = occupant.livesAlone): Record<string, unknown> {
  const has = (id: string) => occupant.conditions.includes(id);
  const cognitive = occupant.conditions.includes("alzheimers")
    ? "alzheimers"
    : occupant.conditions.includes("dementia")
      ? "dementia"
      : null;
  return {
    display_name: occupant.name.trim() || "Resident",
    age: occupant.age && occupant.age > 0 ? occupant.age : 80,
    cognitive_condition: cognitive,
    conditions: occupant.conditions,
    wanders: has("dementia") || has("alzheimers"),
    uses_mobility_aid: has("mobility"),
    anticoagulant: has("blood_thinner"),
    lives_alone: livesAlone,
  };
}

export function conditionLabel(id: string): string {
  return CONDITION_SUGGESTIONS.find((c) => c.id === id)?.label ?? id;
}

export function feedLabelFor(index: number): string {
  return `Camera ${String(index + 1).padStart(2, "0")}`;
}

function openMediaDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(MEDIA_DB, 1);
    request.onupgradeneeded = () => {
      if (!request.result.objectStoreNames.contains(MEDIA_STORE)) {
        request.result.createObjectStore(MEDIA_STORE);
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error ?? new Error("media storage unavailable"));
  });
}

export async function putFeedMedia(id: string, blob: Blob): Promise<void> {
  const db = await openMediaDb();
  await new Promise<void>((resolve, reject) => {
    const tx = db.transaction(MEDIA_STORE, "readwrite");
    tx.objectStore(MEDIA_STORE).put(blob, id);
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error ?? new Error("could not store the video"));
  });
  db.close();
}

export async function getFeedMedia(id: string): Promise<Blob | null> {
  const db = await openMediaDb();
  const blob = await new Promise<Blob | null>((resolve, reject) => {
    const tx = db.transaction(MEDIA_STORE, "readonly");
    const request = tx.objectStore(MEDIA_STORE).get(id);
    request.onsuccess = () => resolve((request.result as Blob | undefined) ?? null);
    request.onerror = () => reject(request.error ?? new Error("could not read the video"));
  });
  db.close();
  return blob;
}

export async function deleteFeedMedia(id: string): Promise<void> {
  const db = await openMediaDb();
  await new Promise<void>((resolve) => {
    const tx = db.transaction(MEDIA_STORE, "readwrite");
    tx.objectStore(MEDIA_STORE).delete(id);
    tx.oncomplete = () => resolve();
    tx.onerror = () => resolve();
  });
  db.close();
}
