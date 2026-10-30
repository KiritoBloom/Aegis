"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { send, type WatchState } from "@/lib/api";
import {
  defaultSetup,
  getFeedMedia,
  loadSetup,
  newOccupant,
  profilePayload,
  saveSetup,
  type ConsoleFeed,
  type ConsoleSetup,
  type FeedMapping,
  type Occupant,
  type SetupProposal,
} from "@/lib/console";
import { sleep } from "@/lib/frame";
import { useApi } from "@/lib/useApi";
import { STEPS, type SetupController, type StepId } from "./controller";
import { CamerasStep } from "./steps/cameras";
import { FinishStep } from "./steps/finish";
import { MappingStep } from "./steps/mapping";
import { PeopleStep } from "./steps/people";
import { ProposalModal } from "./steps/proposal-modal";
import { StartStep } from "./steps/start";
import { WatchStep } from "./steps/watch";
import styles from "./setup.module.css";

type ScanResult = {
  local: boolean;
  width: number;
  height: number;
  person_count: number;
  persons: { box: number[]; posture: string; conf: number }[];
  objects: { label: string; conf: number; box: number[] }[];
  flags: string[];
  status: string;
};

type ProposalResponse = {
  summary: string;
  modes: Record<string, string>;
  notes: string[];
  model: string;
  latency_ms: number;
};

function waitFor(video: HTMLVideoElement, event: string, timeout: number): Promise<void> {
  return new Promise((resolve, reject) => {
    if (event === "loadeddata" && video.readyState >= 2) {
      resolve();
      return;
    }
    const cleanup = () => {
      window.clearTimeout(timer);
      video.removeEventListener(event, done);
      video.removeEventListener("error", fail);
    };
    const done = () => { cleanup(); resolve(); };
    const fail = () => { cleanup(); reject(new Error("The video could not be read.")); };
    const timer = window.setTimeout(() => {
      cleanup();
      reject(new Error("The video did not load in time."));
    }, timeout);
    video.addEventListener(event, done, { once: true });
    video.addEventListener("error", fail, { once: true });
  });
}


export default function SetupPage() {
  const router = useRouter();
  const watch = useApi<WatchState>("/api/watch", 0);
  const [setup, setSetup] = useState<ConsoleSetup | null>(null);
  const [step, setStep] = useState<StepId>("start");
  const [saving, setSaving] = useState(false);
  const [finishError, setFinishError] = useState<string | null>(null);
  const [scanning, setScanning] = useState<string | null>(null);
  const [mapError, setMapError] = useState<string | null>(null);
  const [previews, setPreviews] = useState<Record<string, string>>({});
  const [proposing, setProposing] = useState(false);
  const [museError, setMuseError] = useState<string | null>(null);
  const [showProposal, setShowProposal] = useState(false);
  const [rejectedMuse, setRejectedMuse] = useState(false);
  const [customCondition, setCustomCondition] = useState<Record<string, string>>({});
  const [agreed, setAgreed] = useState(false);

  const setupRef = useRef<ConsoleSetup | null>(null);
  const seededPlan = useRef(false);
  const proposalKey = useRef("");
  const videoUrls = useRef<Record<string, string>>({});

  const mutate = useCallback((change: (current: ConsoleSetup) => ConsoleSetup) => {
    setSetup((current) => {
      if (!current) return current;
      const next = change(current);
      setupRef.current = next;
      saveSetup(next);
      return next;
    });
  }, []);

  useEffect(() => {
    const existing = loadSetup();
    const initial = existing ?? defaultSetup();
    setSetup(initial);
    setupRef.current = initial;
    if (existing?.completed) setAgreed(true);
    const requested = new URLSearchParams(window.location.search).get("step") as StepId | null;
    if (requested && STEPS.some((s) => s.id === requested)) {
      setStep(requested);
    } else if (initial.completed) {
      setStep("finish");
    } else if (!initial.feeds.length && !initial.occupants.some((person) => person.name.trim())) {
      setStep("start");
    } else if (!initial.feeds.length) {
      setStep("cameras");
    } else if (!initial.occupants.some((person) => person.name.trim())) {
      setStep("people");
    } else if (initial.feeds.some((feed) => !initial.mapping[feed.id])) {
      setStep("mapping");
    } else {
      setStep("finish");
    }
  }, []);

  useEffect(() => {
    setupRef.current = setup;
  }, [setup]);

  useEffect(() => {
    if (!watch.data || seededPlan.current) return;
    seededPlan.current = true;
    mutate((current) => {
      if (Object.keys(current.watchModes).length) return current;
      return {
        ...current,
        watchModes: { ...watch.data!.plan.modes },
        quiet: { ...watch.data!.plan.quiet },
        doorHours: watch.data!.plan.door_hours,
      };
    });
  }, [watch.data, mutate]);

  useEffect(() => () => {
    Object.values(videoUrls.current).forEach((url) => URL.revokeObjectURL(url));
  }, []);

  const allMapped = Boolean(
    setup && setup.feeds.length > 0 && setup.feeds.every((feed) => setup.mapping[feed.id]),
  );

  const classes = watch.data?.classes ?? [];

  const counts = useMemo(() => {
    let alert = 0;
    let held = 0;
    let locked = 0;
    for (const spec of classes) {
      const mode = setup?.watchModes[spec.id] ?? spec.default;
      if (spec.locked) locked += 1;
      else if (mode === "alert") alert += 1;
      else held += 1;
    }
    return { alert, held, locked };
  }, [classes, setup?.watchModes]);

  const museApplied = Boolean(
    setup?.proposal &&
      Object.entries(setup.proposal.modes).every(([id, mode]) => setup.watchModes[id] === mode),
  );

  const primary = setup?.occupants[0] ?? null;
  const consentName = setup?.consentName ?? "";

  const resolveFeedUrl = useCallback(async (feed: ConsoleFeed): Promise<string | null> => {
    if (feed.kind === "demo") return feed.demoUrl ?? null;
    if (feed.kind !== "file") return null;
    const cached = videoUrls.current[feed.id];
    if (cached) return cached;
    const blob = await getFeedMedia(feed.id);
    if (!blob) return null;
    const url = URL.createObjectURL(blob);
    videoUrls.current[feed.id] = url;
    return url;
  }, []);

  const captureFrame = useCallback(async (feed: ConsoleFeed): Promise<{ blob: Blob; preview: string }> => {
    let stream: MediaStream | null = null;
    const video = document.createElement("video");
    video.muted = true;
    video.playsInline = true;
    video.preload = "auto";
    try {
      if (feed.kind === "camera") {
        if (!navigator.mediaDevices?.getUserMedia) throw new Error("This browser cannot open a camera.");
        stream = await navigator.mediaDevices.getUserMedia({
          video: feed.deviceId ? { deviceId: { exact: feed.deviceId } } : true,
          audio: false,
        });
        video.srcObject = stream;
        await video.play();
        await sleep(650);
      } else {
        const url = await resolveFeedUrl(feed);
        if (!url) {
          throw new Error("This recording is not stored on this browser. Re-add it in Cameras.");
        }
        video.src = url;
        await waitFor(video, "loadeddata", 9000);
        const duration = Number.isFinite(video.duration) ? video.duration : 0;
        if (duration > 1.4) {
          const target = Math.min(duration * 0.25, 2.5);
          const seeked = waitFor(video, "seeked", 5000);
          video.currentTime = target;
          await seeked;
        }
      }
      if (!video.videoWidth || !video.videoHeight) {
        throw new Error("The feed did not produce a frame. Is it playing?");
      }
      const scale = Math.min(1, 960 / video.videoWidth);
      const canvas = document.createElement("canvas");
      canvas.width = Math.max(1, Math.round(video.videoWidth * scale));
      canvas.height = Math.max(1, Math.round(video.videoHeight * scale));
      const ctx = canvas.getContext("2d");
      if (!ctx) throw new Error("This browser could not draw the frame.");
      ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
      const preview = canvas.toDataURL("image/jpeg", 0.72);
      const blob = await new Promise<Blob>((resolve, reject) => {
        canvas.toBlob(
          (result) => (result ? resolve(result) : reject(new Error("Could not encode the frame."))),
          "image/jpeg",
          0.85,
        );
      });
      return { blob, preview };
    } finally {
      stream?.getTracks().forEach((track) => track.stop());
      video.srcObject = null;
      video.removeAttribute("src");
      video.load();
    }
  }, [resolveFeedUrl]);

  const mapFeed = useCallback(async (feed: ConsoleFeed) => {
    setScanning(feed.id);
    setMapError(null);
    try {
      const frame = await captureFrame(feed);
      const form = new FormData();
      form.append("frame", frame.blob, `${feed.id}.jpg`);
      const response = await fetch("/api/setup/scan", { method: "POST", body: form });
      const text = await response.text();
      if (!response.ok) {
        let detail = `Local mapping failed (${response.status})`;
        try {
          detail = (JSON.parse(text) as { detail?: string }).detail ?? detail;
        } catch { /* keep the status */ }
        throw new Error(detail);
      }
      const result = JSON.parse(text) as ScanResult;
      const mapping: FeedMapping = {
        feedId: feed.id,
        at: new Date().toISOString(),
        width: result.width,
        height: result.height,
        personCount: result.person_count,
        objects: result.objects.map((item) => item.label),
        flags: result.flags,
        status: result.status,
        local: true,
      };
      setPreviews((prev) => ({ ...prev, [feed.id]: frame.preview }));
      mutate((current) => ({
        ...current,
        mapping: { ...current.mapping, [feed.id]: mapping },
        proposal: null,
        configApproved: false,
        configApprovedAt: null,
      }));
      proposalKey.current = "";
      setRejectedMuse(false);
      setMuseError(null);
    } catch (err) {
      setMapError(err instanceof Error ? err.message : "Could not map that camera.");
    } finally {
      setScanning(null);
    }
  }, [captureFrame, mutate]);

  const propose = useCallback(async (force = false): Promise<boolean> => {
    const current = setupRef.current;
    if (!current || !current.museConsent) return false;
    if (!current.feeds.length || current.feeds.some((feed) => !current.mapping[feed.id])) return false;
    const payload = {
      consent: true,
      caregiver: current.caregiver,
      occupants: current.occupants
        .filter((person) => person.name.trim())
        .map((person) => ({
          name: person.name.trim(),
          age: person.age ?? 0,
          relationship: person.relationship,
          conditions: person.conditions,
        })),
      cameras: current.feeds.map((feed) => ({
        name: feed.name.trim() || feed.label,
        location: feed.location.trim(),
        objects: current.mapping[feed.id]?.objects ?? [],
      })),
    };
    if (!payload.occupants.length) {
      setMuseError("Add the name of the person this system is for before Muse can configure it.");
      return false;
    }
    const key = JSON.stringify(payload);
    if (!force && proposalKey.current === key) return false;
    proposalKey.current = key;
    setProposing(true);
    setMuseError(null);
    try {
      const data = await send<ProposalResponse>("/api/setup/propose", payload);
      const proposal: SetupProposal = {
        summary: data.summary,
        modes: data.modes,
        notes: data.notes,
        model: data.model,
        at: new Date().toISOString(),
      };
      mutate((state) => ({ ...state, proposal }));
      setShowProposal(true);
      setRejectedMuse(false);
      return true;
    } catch (err) {
      proposalKey.current = "";
      setMuseError(err instanceof Error ? err.message : "Muse could not configure the plan right now.");
      return false;
    } finally {
      setProposing(false);
    }
  }, [mutate]);

  useEffect(() => {
    if (!setup || step !== "mapping" || !allMapped || !setup.museConsent) return;
    if (setup.proposal || proposing || museError) return;
    void propose(false);
  }, [setup, step, allMapped, proposing, museError, propose]);

  useEffect(() => {
    if (!showProposal) return;
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const close = (event: KeyboardEvent) => {
      if (event.key === "Escape") setShowProposal(false);
    };
    window.addEventListener("keydown", close);
    return () => {
      document.body.style.overflow = previous;
      window.removeEventListener("keydown", close);
    };
  }, [showProposal]);

  const setFeeds = (feeds: ConsoleFeed[]) => {
    mutate((current) => {
      const before = current.feeds.map((feed) => feed.id).sort().join("|");
      const after = feeds.map((feed) => feed.id).sort().join("|");
      if (before === after) return { ...current, feeds };
      const valid = new Set(feeds.map((feed) => feed.id));
      return {
        ...current,
        feeds,
        mapping: Object.fromEntries(
          Object.entries(current.mapping).filter(([id]) => valid.has(id)),
        ),
        proposal: null,
        configApproved: false,
        configApprovedAt: null,
      };
    });
    proposalKey.current = "";
    setMuseError(null);
    setRejectedMuse(false);
  };

  const setOccupant = (id: string, patch: Partial<Occupant>) => {
    mutate((current) => ({
      ...current,
      occupants: current.occupants.map((person) =>
        person.id === id ? { ...person, ...patch } : person,
      ),
    }));
  };

  const toggleCondition = (person: Occupant, condition: string) => {
    const has = person.conditions.includes(condition);
    const next = has
      ? person.conditions.filter((item) => item !== condition)
      : [...person.conditions, condition].slice(0, 12);
    setOccupant(person.id, { conditions: next });
  };

  const addCustomCondition = (person: Occupant) => {
    const value = (customCondition[person.id] ?? "").trim().slice(0, 40);
    if (!value) return;
    if (person.conditions.some((item) => item.toLowerCase() === value.toLowerCase())) {
      setCustomCondition((prev) => ({ ...prev, [person.id]: "" }));
      return;
    }
    setOccupant(person.id, { conditions: [...person.conditions, value].slice(0, 12) });
    setCustomCondition((prev) => ({ ...prev, [person.id]: "" }));
  };

  const addOccupant = () => {
    if (!setup || setup.occupants.length >= 6) return;
    mutate((current) => ({ ...current, occupants: [...current.occupants, newOccupant("Family")] }));
  };

  const removeOccupant = (id: string) => {
    mutate((current) => ({
      ...current,
      occupants: current.occupants.length > 1
        ? current.occupants.filter((person) => person.id !== id)
        : current.occupants,
    }));
  };

  const setMode = (id: string, mode: string) => {
    mutate((current) => ({
      ...current,
      watchModes: { ...current.watchModes, [id]: mode },
      configApproved: false,
      configApprovedAt: null,
    }));
    setRejectedMuse(false);
  };

  const approveProposal = async () => {
    const proposal = setup?.proposal;
    if (!proposal) return;
    setFinishError(null);
    mutate((current) => ({
      ...current,
      watchModes: { ...proposal.modes },
      configApproved: true,
      configApprovedAt: new Date().toISOString(),
      museConsent: true,
    }));
    setShowProposal(false);
    setRejectedMuse(false);
    setStep("finish");
    try {
      await send("/api/watch", {
        modes: proposal.modes,
        door_hours: setup?.doorHours ?? "night",
        quiet: setup?.quiet,
        complete: true,
      }, "PUT");
    } catch (err) {
      setFinishError(
        err instanceof Error
          ? `${err.message}. The plan is approved on this device; finish setup to retry the household sync.`
          : "The household did not accept the plan. Finish setup to retry.",
      );
    }
  };

  const rejectProposal = () => {
    setShowProposal(false);
    setRejectedMuse(true);
    setStep("watch");
  };

  const approveOwnPlan = () => {
    mutate((current) => ({
      ...current,
      configApproved: true,
      configApprovedAt: new Date().toISOString(),
    }));
    setRejectedMuse(false);
  };

  const setMuseConsent = (value: boolean) => {
    mutate((current) => ({ ...current, museConsent: value }));
    setMuseError(null);
    proposalKey.current = "";
  };

  const askMuseAgain = () => {
    mutate((current) => ({
      ...current,
      proposal: null,
      configApproved: false,
      configApprovedAt: null,
    }));
    proposalKey.current = "";
    setMuseError(null);
    setRejectedMuse(false);
    window.setTimeout(() => void propose(true), 0);
  };

  const changeMuseChoices = () => {
    mutate((current) => ({
      ...current,
      configApproved: false,
      configApprovedAt: null,
    }));
    setShowProposal(false);
    setStep("watch");
  };

  const go = (id: StepId) => {
    setStep(id);
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  const next = () => {
    const current = step;
    if (current === "cameras") {
      if (!setup?.feeds.length) return;
      go("people");
    } else if (current === "people") {
      if (!setup?.occupants[0]?.name.trim()) return;
      go("watch");
    } else if (current === "watch") {
      go(allMapped ? "finish" : "mapping");
    } else if (current === "mapping") {
      if (!allMapped) return;
      go("finish");
    }
  };

  const back = () => {
    const index = STEPS.findIndex((item) => item.id === step);
    if (index > 0) go(STEPS[index - 1].id);
  };

  const finish = async () => {
    const current = setupRef.current;
    if (!current || !primary) return;
    if (!current.feeds.length || !current.feeds.every((feed) => current.mapping[feed.id])) {
      setFinishError("Map every camera before finishing.");
      return;
    }
    if (!current.configApproved) {
      setFinishError("Approve the configuration before finishing.");
      return;
    }
    if (consentName.trim().length < 2 || !agreed) {
      setFinishError("The name and consent are required. This is the resident's agreement, not the family's.");
      return;
    }
    setSaving(true);
    setFinishError(null);
    const now = new Date().toISOString();
    const pending: ConsoleSetup = {
      ...current,
      completed: false,
      consentName: consentName.trim(),
      consentAt: now,
    };
    saveSetup(pending);
    setSetup(pending);
    setupRef.current = pending;
    try {
      await send("/api/household", { profile: profilePayload(primary, primary.livesAlone) }, "PUT");
      await send(
        "/api/watch",
        {
          modes: pending.watchModes,
          door_hours: pending.doorHours,
          quiet: pending.quiet,
          complete: true,
          consent_at: now,
          consent_by: consentName.trim(),
        },
        "PUT",
      );
      await send("/api/privacy/consent", {
        given: true,
        member: consentName.trim(),
        device: "household console",
      });
      const complete: ConsoleSetup = { ...pending, completed: true };
      saveSetup(complete);
      setSetup(complete);
      setupRef.current = complete;
      router.push("/");
    } catch (err) {
      setFinishError(
        err instanceof Error
          ? `${err.message}. Nothing was activated; your setup is saved on this device.`
          : "The house could not be reached. Nothing was activated.",
      );
      setSaving(false);
    }
  };

  if (!setup) {
    return (
      <div className={styles.page}>
        <div className={styles.loadingWrap}>
          <div className={styles.loading}>
            <span className={styles.spinner} />
            Opening setup…
          </div>
        </div>
      </div>
    );
  }

  const index = STEPS.findIndex((item) => item.id === step);
  const stepDone = (id: StepId) => {
    if (id === "start") return true;
    if (id === "cameras") return setup.feeds.length > 0;
    if (id === "people") return setup.occupants.some((person) => person.name.trim());
    if (id === "watch") return Object.keys(setup.watchModes).length > 0;
    if (id === "mapping") return allMapped;
    return setup.completed;
  };

  const continueDisabled =
    (step === "cameras" && !setup.feeds.length) ||
    (step === "people" && !primary?.name.trim()) ||
    (step === "mapping" && !allMapped);

  const missingMap = setup.feeds.filter((feed) => !setup.mapping[feed.id]);

  const controller: SetupController = {
    setup, step, watch, classes, counts, allMapped, museApplied, primary, consentName,
    agreed, setAgreed, saving, finishError, scanning, mapError, previews, proposing,
    museError, rejectedMuse, showProposal, setShowProposal, customCondition,
    setCustomCondition, continueDisabled, missingMap, mutate, go, back, next, finish,
    setFeeds, setOccupant, toggleCondition, addCustomCondition, addOccupant,
    removeOccupant, setMode, mapFeed, propose, approveProposal, rejectProposal,
    approveOwnPlan, setMuseConsent, askMuseAgain, changeMuseChoices,
  };

  return (
    <div className={styles.page}>
      <div className={styles.shell}>
        <aside className={styles.rail}>
          <Link href={setup.completed ? "/" : "#"} className={styles.brand}
            onClick={(event) => { if (!setup.completed) event.preventDefault(); }}>
            <span className={styles.wordmark}>Aegis</span>
            <span className={styles.brandNote}>household install</span>
          </Link>
          <div className={styles.railRule} />
          <ol className={styles.steps}>
            {STEPS.map((item, i) => (
              <li key={item.id}>
                <button
                  type="button"
                  className={styles.step}
                  data-state={item.id === step ? "current" : stepDone(item.id) ? "done" : "todo"}
                  onClick={() => go(item.id)}
                  aria-current={item.id === step ? "step" : undefined}
                >
                  <span className={styles.stepNumber}>
                    {stepDone(item.id) && item.id !== step ? "✓" : String(i + 1).padStart(2, "0")}
                  </span>
                  <span className={styles.stepLabel}>
                    {item.label}
                    <span className={styles.stepHint}>{item.hint}</span>
                  </span>
                </button>
              </li>
            ))}
          </ol>
          <div className={styles.railPrivacy}>
            <p>
              <strong>Mapping stays here</strong>
              Frames used to map a room are processed by the local backend and discarded.
              Muse receives the object labels and the answers on this screen — never video,
              never audio, never a face.
            </p>
          </div>
        </aside>

        <main className={styles.content}>
          <div className={styles.kicker}>
            <span className={styles.kickerLine} />
            Step {String(index + 1).padStart(2, "0")} of {String(STEPS.length).padStart(2, "0")}
            {setup.completed ? (
              <Link href="/" className={styles.quiet} style={{ marginLeft: "auto", minHeight: 0, padding: 0 }}>
                Open console
              </Link>
            ) : null}
          </div>

          {step === "start" ? <StartStep c={controller} /> : null}

          {step === "cameras" ? <CamerasStep c={controller} /> : null}

          {step === "people" ? <PeopleStep c={controller} /> : null}

          {step === "watch" ? <WatchStep c={controller} /> : null}

          {step === "mapping" ? <MappingStep c={controller} /> : null}

          {step === "finish" ? <FinishStep c={controller} /> : null}

          {step !== "start" ? (
            <div className={styles.actions}>
              <button type="button" className={styles.secondary} onClick={back} disabled={saving}>
                Back
              </button>
              <span className={styles.spacer} />
              {step === "finish" ? (
                <button
                  type="button"
                  className={styles.primary}
                  onClick={() => void finish()}
                  disabled={saving || !setup.configApproved || !allMapped || consentName.trim().length < 2 || !agreed}
                >
                  {saving ? "Saving…" : setup.completed ? "Save and open console" : "Finish and open console"}
                </button>
              ) : (
                <button
                  type="button"
                  className={styles.primary}
                  onClick={next}
                  disabled={continueDisabled}
                >
                  {step === "mapping" && !allMapped ? "Map every camera first" : "Continue"}
                </button>
              )}
            </div>
          ) : null}

          {step === "mapping" && !allMapped ? (
            <p className={styles.modalNote}>
              Activation stays blocked until every camera has a local mapping.
            </p>
          ) : null}
        </main>
      </div>

      {showProposal && setup.proposal ? <ProposalModal c={controller} /> : null}
    </div>
  );
}
