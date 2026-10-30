import type { Dispatch, SetStateAction } from "react";
import type { WatchClass, WatchState } from "@/lib/api";
import type { ConsoleFeed, ConsoleSetup, Occupant } from "@/lib/console";
import type { Loadable } from "@/lib/useApi";

export type StepId = "start" | "cameras" | "people" | "watch" | "mapping" | "finish";

export const STEPS: { id: StepId; label: string; hint: string }[] = [
  { id: "start", label: "Before we begin", hint: "What Aegis will and will not do" },
  { id: "cameras", label: "Cameras", hint: "Add the rooms you want watched" },
  { id: "people", label: "The household", hint: "Who lives here, and what to know" },
  { id: "watch", label: "Watch preferences", hint: "What deserves your attention" },
  { id: "mapping", label: "Map the views", hint: "Local scan, then Muse configures" },
  { id: "finish", label: "Review & approve", hint: "Consent, configuration, done" },
];

export const MODE_LABEL: Record<string, string> = {
  alert: "Tell me now",
  digest: "Morning note",
  off: "Keep quiet",
};

export const MODE_SHORT: Record<string, string> = {
  alert: "Alert",
  digest: "Digest",
  off: "Quiet",
};

export type SetupController = {
  setup: ConsoleSetup;
  step: StepId;
  watch: Loadable<WatchState>;
  classes: WatchClass[];
  counts: { alert: number; held: number; locked: number };
  allMapped: boolean;
  museApplied: boolean;
  primary: Occupant | null;
  consentName: string;
  agreed: boolean;
  setAgreed: Dispatch<SetStateAction<boolean>>;
  saving: boolean;
  finishError: string | null;
  scanning: string | null;
  mapError: string | null;
  previews: Record<string, string>;
  proposing: boolean;
  museError: string | null;
  rejectedMuse: boolean;
  showProposal: boolean;
  setShowProposal: Dispatch<SetStateAction<boolean>>;
  customCondition: Record<string, string>;
  setCustomCondition: Dispatch<SetStateAction<Record<string, string>>>;
  continueDisabled: boolean;
  missingMap: ConsoleFeed[];
  mutate: (change: (current: ConsoleSetup) => ConsoleSetup) => void;
  go: (id: StepId) => void;
  back: () => void;
  next: () => void;
  finish: () => Promise<void>;
  setFeeds: (feeds: ConsoleFeed[]) => void;
  setOccupant: (id: string, patch: Partial<Occupant>) => void;
  toggleCondition: (person: Occupant, condition: string) => void;
  addCustomCondition: (person: Occupant) => void;
  addOccupant: () => void;
  removeOccupant: (id: string) => void;
  setMode: (id: string, mode: string) => void;
  mapFeed: (feed: ConsoleFeed) => Promise<void>;
  propose: (force?: boolean) => Promise<boolean>;
  approveProposal: () => Promise<void>;
  rejectProposal: () => void;
  approveOwnPlan: () => void;
  setMuseConsent: (value: boolean) => void;
  askMuseAgain: () => void;
  changeMuseChoices: () => void;
};
