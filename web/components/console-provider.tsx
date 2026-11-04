"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { send } from "@/lib/api";
import { useApi, useStream } from "@/lib/useApi";
import type { ConsoleToast, FeedSource, LiveFeed, LogEntry, PipelineStep, WallAlert, WallFrame } from "@/lib/console-types";
import { captureObservation, sleep } from "@/lib/frame";
import { CaptureVideo } from "@/components/capture-video";
import {
  getFeedMedia,
  clearLocalConsoleData,
  defaultSetup,
  loadSetup,
  primaryOccupant,
  saveSetup,
  setupReadyToWatch,
  type ConsoleFeed,
  type ConsoleSetup,
  type Occupant,
} from "@/lib/console";

type ConsoleContextValue = {
  ready: boolean;
  setup: ConsoleSetup | null;
  resident: Occupant | null;
  active: boolean;
  switching: boolean;
  setActive: (on: boolean) => Promise<void>;
  feeds: ConsoleFeed[];
  updateSetup: (mutate: (setup: ConsoleSetup) => ConsoleSetup) => void;
  sources: Record<string, FeedSource>;
  live: Record<string, LiveFeed>;
  logs: Record<string, LogEntry[]>;
  pipeline: PipelineStep[];
  pipelineError: string | null;
  clearLogs: (feedId?: string) => void;
  toasts: ConsoleToast[];
  dismissToast: (id: number) => void;
  blockedReason: string | null;
  clearBlocked: () => void;
};

const ConsoleContext = createContext<ConsoleContextValue | null>(null);
const LOG_KEY = "aegis.console.logs.v1";

function sessionId(): string {
  const c = globalThis.crypto as Crypto | undefined;
  return `console_${c?.randomUUID?.() ?? `${Date.now()}_${Math.random().toString(36).slice(2)}`}`;
}

function requestSignal(parent: AbortSignal): AbortSignal {
  const timeout = (AbortSignal as typeof AbortSignal & {
    timeout?: (ms: number) => AbortSignal;
    any?: (signals: AbortSignal[]) => AbortSignal;
  });
  if (timeout.timeout && timeout.any) {
    return timeout.any([parent, timeout.timeout(30000)]);
  }
  return parent;
}

function loadLogs(): Record<string, LogEntry[]> {
  if (typeof window === "undefined") return {};
  try {
    const raw = window.localStorage.getItem(LOG_KEY);
    return raw ? (JSON.parse(raw) as Record<string, LogEntry[]>) : {};
  } catch {
    return {};
  }
}

export function ConsoleProvider({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const [setup, setSetup] = useState<ConsoleSetup | null>(null);
  const [ready, setReady] = useState(false);
  const [active, setActiveState] = useState(false);
  const [switching, setSwitching] = useState(false);
  const [sources, setSources] = useState<Record<string, FeedSource>>({});
  const [live, setLive] = useState<Record<string, LiveFeed>>({});
  const [logs, setLogs] = useState<Record<string, LogEntry[]>>({});
  const [pipeline, setPipeline] = useState<PipelineStep[]>([]);
  const diagnostics = useApi<{ items: PipelineStep[] }>("/api/diagnostics?limit=300", 15000);
  const mergePipeline = useCallback((rows: PipelineStep[]) => {
    setPipeline((old) => [...new Map([...old, ...rows].map((row) => [row.id, row])).values()]
      .sort((a, b) => a.id - b.id).slice(-500));
  }, []);
  useEffect(() => { if (diagnostics.data?.items) mergePipeline(diagnostics.data.items); }, [diagnostics.data, mergePipeline]);
  const [toasts, setToasts] = useState<ConsoleToast[]>([]);
  const [blockedReason, setBlockedReason] = useState<string | null>(null);

  const setupRef = useRef<ConsoleSetup | null>(null);
  const sourcesRef = useRef<Record<string, FeedSource>>({});
  const objectUrlsRef = useRef<Record<string, string>>({});
  const cameraStreamsRef = useRef<Record<string, MediaStream>>({});
  const openingCameras = useRef(new Set<string>());
  const switchingRef = useRef(false);
  const captureRefs = useRef<Record<string, HTMLVideoElement | null>>({});
  const workersRef = useRef<Map<string, AbortController>>(new Map());
  const sessionRef = useRef<string | null>(null);
  const logSeq = useRef(0);
  const toastSeq = useRef(0);
  const toastTimers = useRef(new Set<number>());
  const feedStateRef = useRef<Record<string, { started: boolean; persons: number; posture: string; objects: string; alertId: string | null; lastPosture: number; lastHeartbeat: number }>>({});
  const verificationRef = useRef<Record<string, boolean>>({});
  const seenIncidentsRef = useRef<Record<string, boolean>>({});
  const publishedIncidents = useRef(new Set<string>());
  const settledIncidents = useRef(new Map<string, "recovered" | "acknowledged">());
  const hydratedLogs = useRef(false);
  const endedRef = useRef<Record<string, boolean>>({});
  const activeRef = useRef(false);

  const resident = setup ? primaryOccupant(setup) : null;
  const residentName = resident?.name?.trim() || "Resident";

  const addLog = useCallback((feedId: string, source: LogEntry["source"], tone: LogEntry["tone"], message: string, detail?: string) => {
    logSeq.current += 1;
    const entry: LogEntry = { id: logSeq.current, at: Date.now(), source, tone, message, detail };
    setLogs((prev) => ({
      ...prev,
      [feedId]: [...(prev[feedId] ?? []), entry].slice(-140),
    }));
  }, []);

  const toast = useCallback((tone: ConsoleToast["tone"], title: string, detail?: string, incidentId?: string) => {
    toastSeq.current += 1;
    const id = toastSeq.current;
    setToasts((prev) => [...prev.filter((item) => !incidentId || item.incidentId !== incidentId),
      { id, tone, title, detail, incidentId }].slice(-3));
    const timer = window.setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
      toastTimers.current.delete(timer);
    }, 9000);
    toastTimers.current.add(timer);
  }, []);

  const dismissToast = useCallback((id: number) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  useEffect(() => {
    const loaded = loadSetup();
    if (!loaded || !loaded.completed) {
      if (["/", "/logs", "/settings", "/live"].includes(pathname)) router.replace("/setup");
      setSetup(loaded ?? defaultSetup());
      setupRef.current = loaded ?? null;
      setReady(true);
      return;
    }
    setSetup(loaded);
    setupRef.current = loaded;
    if (!hydratedLogs.current) {
      const storedLogs = loadLogs();
      logSeq.current = Object.values(storedLogs).flat().reduce((max, entry) => Math.max(max, entry.id), 0);
      setLogs(storedLogs);
      hydratedLogs.current = true;
    }
    setReady(true);
  }, [router, pathname]);

  useEffect(() => {
    setupRef.current = setup;
  }, [setup]);

  useEffect(() => {
    sourcesRef.current = sources;
  }, [sources]);

  useEffect(() => {
    activeRef.current = active;
  }, [active]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      if (typeof window !== "undefined") {
        try { window.localStorage.setItem(LOG_KEY, JSON.stringify(logs)); }
        catch { /* Telemetry must not crash capture when browser storage is full. */ }
      }
    }, 500);
    return () => window.clearTimeout(timer);
  }, [logs]);

  useEffect(() => {
    const stopForUnload = () => {
      if (!activeRef.current) return;
      const session = sessionRef.current;
      if (session) {
        void fetch("/api/wall/session/close", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ session }),
          keepalive: true,
        }).catch(() => {});
      }
      void fetch("/api/privacy/pause", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ by: "household console", reason: "console closed" }),
        keepalive: true,
      }).catch(() => {});
    };
    window.addEventListener("beforeunload", stopForUnload);
    return () => {
      window.removeEventListener("beforeunload", stopForUnload);
      stopForUnload();
      workersRef.current.forEach((controller) => controller.abort());
      Object.values(objectUrlsRef.current).forEach((url) => URL.revokeObjectURL(url));
      Object.values(cameraStreamsRef.current).forEach((stream) => stream.getTracks().forEach((t) => t.stop()));
      toastTimers.current.forEach((timer) => window.clearTimeout(timer));
    };
  }, []);

  // Metadata edits must not revoke the URL of a recording being analysed.
  const sourceKey = JSON.stringify(setup?.feeds.map((feed) => [feed.id, feed.kind, feed.demoUrl]));
  useEffect(() => {
    if (!setup) return;
    let cancelled = false;
    (async () => {
      const next: Record<string, FeedSource> = {};
      for (const feed of setup.feeds) {
        if (feed.kind === "demo") {
          next[feed.id] = { url: feed.demoUrl };
          continue;
        }
        if (feed.kind === "camera") {
          next[feed.id] = sourcesRef.current[feed.id] ?? {};
          continue;
        }
        try {
          const blob = await getFeedMedia(feed.id);
          if (!blob) {
            next[feed.id] = { error: "This local video is not stored on this browser. Re-add it in Settings." };
            continue;
          }
          const previous = objectUrlsRef.current[feed.id];
          if (previous) { next[feed.id] = { url: previous }; continue; }
          if (cancelled) return;
          const url = URL.createObjectURL(blob);
          objectUrlsRef.current[feed.id] = url;
          next[feed.id] = { url };
        } catch {
          next[feed.id] = { error: "Could not open the stored video." };
        }
      }
      if (cancelled) return;
      for (const id of Object.keys(objectUrlsRef.current)) {
        if (!next[id]?.url) {
          URL.revokeObjectURL(objectUrlsRef.current[id]);
          delete objectUrlsRef.current[id];
        }
      }
      sourcesRef.current = next;
      setSources(next);
    })();
    return () => { cancelled = true; };
  }, [sourceKey]);

  const updateSetup = useCallback((mutate: (current: ConsoleSetup) => ConsoleSetup) => {
    setSetup((current) => {
      if (!current) return current;
      const next = mutate(current);
      setupRef.current = next;
      saveSetup(next);
      return next;
    });
  }, []);

  const addLogForAll = useCallback((source: LogEntry["source"], tone: LogEntry["tone"], message: string, detail?: string) => {
    const current = setupRef.current;
    if (!current) return;
    for (const feed of current.feeds) addLog(feed.id, source, tone, `${feed.label} · ${message}`, detail);
    if (!current.feeds.length) addLog("system", source, tone, message, detail);
  }, [addLog]);

  const noteVerification = useCallback((feed: ConsoleFeed, alert: WallAlert) => {
    if (alert.verification?.timeline) return;
    const status = alert.verification?.status;
    if (!status) return;
    const stage = alert.verification?.stage || "";
    const reason = alert.verification?.reason || "";
    // One row per thing said, not per (status, stage, reason) triple. The frame
    // response reports the same stage as `pending` with different wording, so
    // keying on all three printed every stage twice.
    const message = status === "verifying" || status === "pending"
      ? stage === "sam" ? "Evidence clip collected · SAM is tracking"
        : stage === "muse" ? "SAM finished · Muse is deciding"
          : "Collecting the evidence clip"
      : status === "verified" ? "Evidence verified · resident check-in"
        : status === "rejected" ? "Not corroborated"
          : status === "unavailable" ? "Verification unavailable"
            : status === "recovered" ? "Verification cleared" : "";
    if (!message) return;
    const key = `${alert.id}:${message}`;
    if (verificationRef.current[key]) return;
    verificationRef.current[key] = true;
    if (status === "verifying" || status === "pending") {
      addLog(feed.id, "Model", "info", message,
        reason || "No family alert has been sent yet.");
    } else if (status === "verified") {
      addLog(feed.id, "Model", "info", message, reason || "The resident is asked before a family alert is released.");
    } else if (status === "rejected") {
      addLog(feed.id, "Alert", "warn", message, reason || "No family alert was sent.");
      toast("warn", message, `${feed.label} · no family alert was sent.`);
    } else if (status === "unavailable") {
      addLog(feed.id, "System", "warn", message, reason || "The local safety floor still applies; waiting for the resident check-in.");
    } else if (status === "recovered") {
      addLog(feed.id, "Alert", "good", message, reason || "The resident stood up.");
    }
  }, [addLog, toast]);

  const applyFrame = useCallback((feed: ConsoleFeed, data: WallFrame, observation: NonNullable<LiveFeed["observation"]>) => {
    if (data.paused) { activeRef.current = false; setActiveState(false); return; }
    for (const alert of [data.alert, data.last_alert]) {
      if (!alert?.incident_id) continue;
      if (settledIncidents.current.has(alert.incident_id)) {
        alert.active = false;
        alert.recovered = settledIncidents.current.get(alert.incident_id) === "recovered";
        alert.verification = { ...alert.verification, status: settledIncidents.current.get(alert.incident_id) };
      } else if (publishedIncidents.current.has(alert.incident_id)) {
        alert.verification = { ...alert.verification, status: "notified", escalated: true };
      }
    }
    setLive((prev) => ({
      ...prev,
      [feed.id]: { frame: data, observation, status: observation.ended ? "ended" : "watching", receivedAt: Date.now() },
    }));
    const state = feedStateRef.current[feed.id] ?? {
      started: false, persons: 0, posture: "none", objects: "", alertId: null,
      lastPosture: 0, lastHeartbeat: 0,
    };
    const now = Date.now();
    const primary = data.persons.find((person) => person.primary) ?? data.persons[0];
    const posture = primary?.posture ?? "none";
    const objects = [...new Set(data.objects.map((o) => o.label))].sort().join("|");
    if (!state.started) {
      state.started = true;
      addLog(feed.id, "Vision", "good", "Tracking started",
        `${data.person_count} ${data.person_count === 1 ? "person" : "people"} · ${posture} · acquiring track${primary?.track_id ? ` #${primary.track_id}` : ""}`);
      state.lastHeartbeat = now;
    } else {
      if (state.persons === 0 && data.person_count > 0) {
        addLog(feed.id, "Vision", "good", "Person entered",
          `${data.person_count} ${data.person_count === 1 ? "person" : "people"}${primary?.track_id ? ` · track #${primary.track_id}` : ""}`);
      } else if (state.persons > 0 && data.person_count === 0) {
        addLog(feed.id, "Vision", "info", "Person not detected", "Rechecking the observation. Absence alone does not count as recovery.");
      }
      if (posture !== "none" && posture !== state.posture && now - state.lastPosture > 1200) {
        addLog(feed.id, "Vision", posture === "lying" ? "warn" : "info",
          `Posture ${posture}`,
          `Track #${primary?.track_id ?? "—"} · ${state.posture} → ${posture} · ${primary?.support_surface ? `supported by ${primary.support_surface}` : "no supporting furniture confirmed"}`);
        state.lastPosture = now;
      }
      if (objects && objects !== state.objects) {
        addLog(feed.id, "Vision", "info", "Room context", objects.split("|").join(", "));
      }
      if (now - state.lastHeartbeat > 20000) {
        addLog(feed.id, "Vision", "info", "Steady",
          `${data.person_count} ${data.person_count === 1 ? "person" : "people"} · ${posture} · ${data.tracking.fps || "sampling"} fps · ${data.tracking.processing_ms ?? "—"} ms analysis · ${data.tracking.queue_ms ?? "—"} ms queue`);
        state.lastHeartbeat = now;
      }
    }
    state.persons = data.person_count;
    state.posture = posture;
    state.objects = objects;
    feedStateRef.current[feed.id] = state;

    if (data.alert) {
      const isNew = data.alert.id !== state.alertId;
      state.alertId = data.alert.id;
      if (isNew) {
        const what = data.alert.kind === "possible_fall"
          ? `${residentName} may have fallen`
            : "More than one person in view";
        addLog(feed.id, "Alert", data.alert.severity === "alert" ? "alert" : "warn",
          what, `Local candidate only · ${data.alert.evidence?.lying_for_s?.toFixed(1) ?? "—"}s down · evidence verification starts before any family alert`);
        if (data.alert.kind !== "possible_fall") toast("warn", what, `${feed.name || feed.label} · review the feed`);
      }
      noteVerification(feed, data.alert);
    }
    if (data.last_alert) noteVerification(feed, data.last_alert);
    if (data.resolved && !verificationRef.current[`${data.resolved.id}:recovery`]) {
      verificationRef.current[`${data.resolved.id}:recovery`] = true;
      addLog(feed.id, "Alert", "good", "Condition cleared", "The person is upright; recovery recorded.");
    }
  }, [addLog, noteVerification, residentName, toast]);

  const worker = useCallback(async (feed: ConsoleFeed, session: string, signal: AbortSignal) => {
    let failures = 0;
    let lastMediaTime = -1;
    while (!signal.aborted) {
      const video = captureRefs.current[feed.id];
      if (!video || video.readyState < 2 || !video.videoWidth) {
        await sleep(280, signal);
        continue;
      }
      if (video.ended) {
        if (!video.loop) {
          if (endedRef.current[feed.id]) {
            await sleep(400, signal);
            continue;
          }
          // Analyse the final decoded image once before freezing the view.
        } else {
          endedRef.current[feed.id] = false;
          video.currentTime = 0;
          await video.play().catch(() => {});
          await sleep(200, signal);
          continue;
        }
      }
      if (!video.ended) endedRef.current[feed.id] = false;
      if (video.paused && !video.ended) {
        // Activation is a user gesture; muted playback should be allowed. If
        // the browser suspended an offscreen element, nudge it back to life.
        void video.play().catch(() => {});
        await sleep(240, signal);
        if (video.paused) {
          await sleep(320, signal);
          continue;
        }
      }
      if (!video.ended && Math.abs(video.currentTime - lastMediaTime) < 0.001) {
        await sleep(32, signal);
        continue;
      }
      const requestStarted = performance.now();
      try {
        const captured = await captureObservation(video);
        if (!captured) { await sleep(400, signal); continue; }
        if (signal.aborted) return;
        const { blob, ...observation } = captured;
        const form = new FormData();
        form.append("session", session);
        const currentFeed = setupRef.current?.feeds.find((item) => item.id === feed.id) ?? feed;
        form.append("feed", JSON.stringify({ id: feed.id, name: currentFeed.name || currentFeed.label, kind: feed.kind, location: currentFeed.location }));
        form.append("frame", blob, `${feed.id}.jpg`);
        const res = await fetch("/api/wall/frame", {
          method: "POST", body: form, signal: requestSignal(signal),
        });
        if (!res.ok) {
          const text = await res.text();
          let detail = `analysis failed (${res.status})`;
          try { detail = (JSON.parse(text) as { detail?: string }).detail || detail; } catch { /* keep */ }
          throw new Error(detail);
        }
        const data = (await res.json()) as WallFrame;
        if (signal.aborted) return;
        lastMediaTime = captured.mediaTime;
        data.tracking = { ...data.tracking, round_trip_ms: Math.round(performance.now() - requestStarted),
          media_delay_ms: Math.round(Math.max(0, video.currentTime - captured.mediaTime) * 1000) };
        failures = 0;
        applyFrame(feed, data, observation);
        if (captured.ended) {
          endedRef.current[feed.id] = true;
          addLog(feed.id, "System", "info", "Recording finished", "Showing the final frame. Loop is off.");
        }
      } catch (err) {
        if (signal.aborted) return;
        failures += 1;
        const message = err instanceof Error ? err.message : "the local backend did not answer";
        setLive((prev) => ({ ...prev, [feed.id]: { ...(prev[feed.id] ?? { status: "error" }), status: "error", error: message } }));
        if (failures === 1 || failures % 8 === 0) {
          addLog(feed.id, "System", "warn", "Analysis interrupted", `${message} · retrying`);
        }
        await sleep(Math.min(4000, 500 * failures), signal);
      }
      await sleep(60, signal);
    }
  }, [addLog, applyFrame]);

  const stopWorkers = useCallback(() => {
    workersRef.current.forEach((controller) => controller.abort());
    workersRef.current.clear();
  }, []);

  const stopCameras = useCallback(() => {
    Object.values(cameraStreamsRef.current).forEach((stream) => stream.getTracks().forEach((track) => track.stop()));
    cameraStreamsRef.current = {};
    setSources((prev) => {
      const next = { ...prev };
      for (const id of Object.keys(next)) {
        if (next[id].stream) next[id] = { ...next[id], stream: undefined, error: "Camera disconnected" };
      }
      return next;
    });
  }, []);

  const startCameras = useCallback(async () => {
    const current = setupRef.current;
    if (!current) return;
    for (const feed of current.feeds.filter((f) => f.kind === "camera" && current.mapping[f.id])) {
      if (cameraStreamsRef.current[feed.id] || openingCameras.current.has(feed.id)) continue;
      openingCameras.current.add(feed.id);
      try {
        const stream = await navigator.mediaDevices.getUserMedia({
          video: feed.deviceId ? { deviceId: { exact: feed.deviceId } } : true,
          audio: false,
        });
        if (!activeRef.current) { stream.getTracks().forEach((t) => t.stop()); continue; }
        cameraStreamsRef.current[feed.id] = stream;
        setSources((prev) => ({ ...prev, [feed.id]: { ...prev[feed.id], stream, error: undefined } }));
        addLog(feed.id, "System", "good", "Camera connected");
      } catch {
        setSources((prev) => ({ ...prev, [feed.id]: { ...prev[feed.id], error: "Camera permission is not available." } }));
        addLog(feed.id, "System", "warn", "Camera not connected", "Check the browser permission.");
      } finally {
        openingCameras.current.delete(feed.id);
      }
    }
  }, [addLog]);

  const setActive = useCallback(async (on: boolean) => {
    if (switchingRef.current || on === activeRef.current) return;
    switchingRef.current = true;
    setSwitching(true);
    try {
    if (on) {
      const current = setupRef.current;
      if (!current) return;
      const readiness = setupReadyToWatch(current);
      if (!readiness.ready) {
        setBlockedReason(readiness.reason);
        return;
      }
      const unavailable = current.feeds.find((feed) => feed.kind !== "camera" && !sourcesRef.current[feed.id]?.url);
      if (unavailable) {
        setBlockedReason(sourcesRef.current[unavailable.id]?.error || `${unavailable.name || unavailable.label} is still opening. Retry when the recording is ready.`);
        return;
      }
      try {
        // Consent is a precondition for watch mode, not a detail we can ignore.
        await send("/api/privacy/resume", {});
      } catch (err) {
        const reason = err instanceof Error ? err.message : "Monitoring cannot resume.";
        setBlockedReason(reason);
        addLogForAll("System", "warn", "Watching blocked", reason);
        return;
      }
      if (!sessionRef.current) sessionRef.current = sessionId();
      activeRef.current = true;
      endedRef.current = {};
      setActiveState(true);
      setLive(Object.fromEntries(current.feeds.map((feed) => [
        feed.id, { status: "idle" as const, receivedAt: Date.now() },
      ])));
      addLogForAll("System", "good", "Watching started", "Local tracking is active.");
      await startCameras();
    } else {
      activeRef.current = false;
      setActiveState(false);
      stopWorkers();
      stopCameras();
      const session = sessionRef.current;
      sessionRef.current = null;
      if (session) {
        void fetch("/api/wall/session/close", {
          method: "POST", headers: { "content-type": "application/json" },
          body: JSON.stringify({ session }), keepalive: true,
        }).catch(() => {});
      }
      try {
        await send("/api/privacy/pause", { by: "household console", reason: "monitoring switched off from the console" });
      } catch (err) {
        toast("warn", "Local capture stopped", err instanceof Error ? `The backend pause could not be confirmed: ${err.message}` : "The backend pause could not be confirmed.");
      }
      addLogForAll("System", "info", "Watching paused", "No frames are being analyzed.");
      setLive({});
    }
    } finally {
      switchingRef.current = false;
      setSwitching(false);
    }
  }, [addLogForAll, startCameras, stopCameras, stopWorkers, toast]);

  useEffect(() => {
    if (active && (pathname === "/setup" || !setupReadyToWatch(setup).ready)) {
      void setActive(false);
    }
  }, [active, pathname, setup, setActive]);

  // One independent worker per feed, reconciled as feeds and sources change.
  useEffect(() => {
    if (!active) {
      stopWorkers();
      return;
    }
    const session = sessionRef.current;
    if (!session) return;
    for (const feed of setup?.feeds ?? []) {
      if (!setup?.mapping[feed.id]) continue;
      const source = sources[feed.id];
      if (!source || (!source.url && !source.stream)) continue;
      if (workersRef.current.has(feed.id)) continue;
      const controller = new AbortController();
      workersRef.current.set(feed.id, controller);
      void worker(feed, session, controller.signal);
    }
    for (const [id, controller] of Array.from(workersRef.current.entries())) {
      const feed = (setup?.feeds ?? []).find((f) => f.id === id);
      if (!feed || !setup?.mapping[id]) {
        controller.abort();
        workersRef.current.delete(id);
      }
    }
  }, [active, setup?.feeds, sources, stopWorkers, worker]);

  // A camera added from Settings while the console is live should open now,
  // not after the next activation. A removed camera should release its tracks.
  useEffect(() => {
    if (!setup) return;
    if (active) void startCameras();
    else stopCameras();
    const currentIds = new Set(
      setup.feeds
        .filter((feed) => feed.kind === "camera" && setup.mapping[feed.id])
        .map((feed) => feed.id),
    );
    for (const [id, stream] of Object.entries(cameraStreamsRef.current)) {
      if (currentIds.has(id)) continue;
      stream.getTracks().forEach((track) => track.stop());
      delete cameraStreamsRef.current[id];
      setSources((prev) => {
        if (!prev[id]) return prev;
        const next = { ...prev };
        delete next[id];
        return next;
      });
    }
  }, [active, setup, startCameras, stopCameras]);

  // Cross-tab alerts from the real pipeline, independent of the camera wall.
  useStream((frame, payload) => {
    if (frame === "pipeline.step") {
      mergePipeline([(payload as { step: PipelineStep }).step]);
      return;
    }
    if (frame === "snapshot" && (payload as { pipeline?: PipelineStep[] }).pipeline) {
      mergePipeline((payload as { pipeline: PipelineStep[] }).pipeline);
    }
    const event = payload as Record<string, unknown>;
    if (frame === "wall.verification") {
      // Verification stages and verdicts arrive on the stream, so they keep
      // advancing after frames stop (recording ended, slow feed).
      const feedId = String(event.feed_id || "");
      const alert = event.alert as WallAlert | undefined;
      if (!feedId || !alert) return;
      const feed = (setupRef.current?.feeds ?? []).find((f) => f.id === feedId);
      if (feed) noteVerification(feed, alert);
      setLive((prev) => {
        const state = prev[feedId];
        const current = state?.frame;
        if (!state || !current) return prev;
        const patch = (a: WallAlert | null | undefined): WallAlert | null =>
          a && a.id === alert.id
             ? { ...a, verification: a.incident_id && settledIncidents.current.has(a.incident_id)
               ? { ...alert.verification, status: settledIncidents.current.get(a.incident_id) }
               : a.incident_id && publishedIncidents.current.has(a.incident_id)
                 ? { ...alert.verification, status: "notified", escalated: true } : alert.verification,
               active: a.incident_id && settledIncidents.current.has(a.incident_id) ? false : a.active,
                recovered: a.incident_id && settledIncidents.current.has(a.incident_id)
                  ? settledIncidents.current.get(a.incident_id) === "recovered" : alert.recovered }
            : (a ?? null);
        const nextAlert = patch(current.alert);
        const nextLast = patch(current.last_alert);
        if (nextAlert === current.alert && nextLast === current.last_alert) return prev;
        return {
          ...prev,
          [feedId]: { ...state, frame: { ...current, alert: nextAlert, last_alert: nextLast } },
        };
      });
      return;
    }
    if (frame === "privacy.paused" || frame === "privacy.deleted"
        || (frame === "snapshot" && (event.pause as { paused?: boolean } | undefined)?.paused && activeRef.current)) {
      activeRef.current = false;
      setActiveState(false);
      stopWorkers();
      stopCameras();
      setLive({});
      sessionRef.current = null;
      if (frame === "privacy.deleted") {
        clearLocalConsoleData();
        window.localStorage.removeItem(LOG_KEY);
        setToasts([]);
        setLogs({});
        setPipeline([]);
        diagnostics.setData({ items: [] });
        setSetup(null);
        setupRef.current = null;
      }
      return;
    }
    if (frame === "incident.notified") {
      const id = String(event.id || "");
      const key = `${id}:${event.phase || 1}`;
      if (!id || settledIncidents.current.has(id) || seenIncidentsRef.current[key]) return;
      seenIncidentsRef.current[key] = true;
      publishedIncidents.current.add(id);
      const channels = Array.isArray(event.channels) ? event.channels.map(String) : [];
      toast("alert", "An alert needs attention", channels.some((c) => ["webpush", "sms", "webhook"].includes(c))
        ? "A family delivery channel accepted the alert. Open Events to respond."
        : "The alert is available in this console. Open Events to respond.", id);
      addLog("system", "Alert", "alert", "Alert released", `${id} · ${channels.join(", ") || "console"}`);
      setLive((prev) => Object.fromEntries(Object.entries(prev).map(([feedId, state]) => {
        if (!state.frame) return [feedId, state];
        const publish = (alert: WallAlert | null | undefined) => alert?.incident_id === id
          ? { ...alert, verification: { ...alert.verification, status: "notified", escalated: true } } : alert;
        return [feedId, { ...state, frame: { ...state.frame,
          alert: publish(state.frame.alert) ?? null, last_alert: publish(state.frame.last_alert) } }];
      })));
    } else if (frame === "incident.decided") {
      if (event.resident_first) return;
      const id = String(event.id || "");
      if (!id || seenIncidentsRef.current[id]) return;
      seenIncidentsRef.current[id] = true;
      const title = String(event.title || "An alert needs attention");
      const severity = String(event.severity || "watch");
      const named = title.toLowerCase().startsWith(residentName.toLowerCase())
        ? title
        : `${residentName}: ${title}`;
      addLog("system", "Model", "info", named, `${severity} · evidence updated`);
    } else if (frame === "checkin.started") {
      toast("info", `Check-in started for ${residentName}`, "Waiting for an answer.", String(event.id || ""));
      addLog("system", "System", "warn", `Check-in started for ${residentName}`);
    } else if (frame === "checkin.no_answer") {
      if (event.escalated) toast("alert", `No answer from ${residentName}`, "The check-in timed out. Open Events to respond.", String(event.id || ""));
      addLog("system", "Alert", "alert", `No answer from ${residentName}`);
    } else if (frame === "checkin.answered") {
      const id = String(event.id || "");
      if (event.resolution !== "already_closed" && !publishedIncidents.current.has(id)) {
        const recovered = event.resolution === "self_recovered";
        toast(recovered ? "good" : "info", `${residentName} answered`,
          recovered ? "Recovery confirmed. No further response is needed." : "The answer is recorded; the safety check may still need attention.", id);
      }
      addLog("system", "System", "info", `${residentName} answered the check-in`);
    }
    if (frame === "incident.recovered" || frame === "incident.ack"
        || (frame === "checkin.answered" && event.resolution === "self_recovered")) {
      const id = String(event.id || "");
      if (!id) return;
      const outcome = frame === "incident.ack" ? "acknowledged" : "recovered";
      settledIncidents.current.set(id, outcome);
      setToasts((prev) => prev.filter((item) => item.incidentId !== id || item.tone === "good"));
      setLive((prev) => Object.fromEntries(Object.entries(prev).map(([feedId, state]) => {
        if (!state.frame) return [feedId, state];
        const settle = (alert: WallAlert | null | undefined) => alert?.incident_id === id
          ? { ...alert, active: false, recovered: outcome === "recovered",
            verification: { ...alert.verification, status: outcome } } : alert;
        return [feedId, { ...state, frame: { ...state.frame,
          alert: settle(state.frame.alert) ?? null, last_alert: settle(state.frame.last_alert) } }];
      })));
      addLog("system", "Alert", "good", frame === "incident.ack" ? "Family response recorded" : "Recovery recorded", id);
    }
  });

  const clearLogs = useCallback((feedId?: string) => {
    setLogs((prev) => {
      if (!feedId) return {};
      const next = { ...prev };
      delete next[feedId];
      return next;
    });
  }, []);

  const combinedLogs = useMemo(() => {
    const result = Object.fromEntries(Object.entries(logs).map(([id, entries]) => [id, [...entries]]));
    for (const step of pipeline) {
      const source: LogEntry["source"] = ["sam", "muse"].includes(step.stage) ? "Model"
        : step.stage === "local" ? "Vision" : step.stage === "delivery" ? "Alert" : "System";
      (result[step.feed_id] ??= []).push({ id: -step.id, pipelineId: step.id,
        at: new Date(step.created_at).getTime(), source,
        tone: step.status === "failed" ? "warn" : step.status === "done" ? "good" : "info",
        message: `${step.title}${step.status === "running" ? " · started" : ""}`,
        detail: [step.detail, step.metrics.duration_ms != null ? `${(step.metrics.duration_ms / 1000).toFixed(1)}s` : ""].filter(Boolean).join(" · ") });
    }
    for (const entries of Object.values(result)) entries.sort((a, b) => a.at - b.at || b.id - a.id);
    return result;
  }, [logs, pipeline]);

  const value = useMemo<ConsoleContextValue>(() => ({
    ready,
    setup,
    resident,
    active,
    switching,
    setActive,
    feeds: setup?.feeds ?? [],
    updateSetup,
    sources,
    live,
    logs: combinedLogs,
    pipeline,
    pipelineError: diagnostics.error,
    clearLogs,
    toasts,
    dismissToast,
    blockedReason,
    clearBlocked: () => setBlockedReason(null),
  }), [ready, setup, resident, active, switching, setActive, updateSetup, sources, live, combinedLogs, pipeline, diagnostics.error, clearLogs, toasts, dismissToast, blockedReason]);

  return (
    <ConsoleContext.Provider value={value}>
      {children}
      <div aria-hidden="true" className="console-capture">
        {(active ? setup?.feeds ?? [] : []).map((feed) => {
          const source = sources[feed.id];
          if (!source?.url && !source?.stream) return null;
          return (
            <CaptureVideo
              key={feed.id}
              feed={feed}
              source={source}
              register={(el) => { captureRefs.current[feed.id] = el; }}
            />
          );
        })}
      </div>
    </ConsoleContext.Provider>
  );
}

export function useConsole(): ConsoleContextValue {
  const value = useContext(ConsoleContext);
  if (!value) throw new Error("useConsole must be used inside ConsoleProvider");
  return value;
}
