"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { send } from "@/lib/api";
import { useStream } from "@/lib/useApi";
import type { ConsoleToast, FeedSource, LiveFeed, LogEntry, WallAlert, WallFrame } from "@/lib/console-types";
import { captureFrame, sleep } from "@/lib/frame";
import { CaptureVideo } from "@/components/capture-video";
import {
  getFeedMedia,
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
  setActive: (on: boolean) => Promise<void>;
  feeds: ConsoleFeed[];
  updateSetup: (mutate: (setup: ConsoleSetup) => ConsoleSetup) => void;
  sources: Record<string, FeedSource>;
  live: Record<string, LiveFeed>;
  logs: Record<string, LogEntry[]>;
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
  const [setup, setSetup] = useState<ConsoleSetup | null>(null);
  const [ready, setReady] = useState(false);
  const [active, setActiveState] = useState(false);
  const [sources, setSources] = useState<Record<string, FeedSource>>({});
  const [live, setLive] = useState<Record<string, LiveFeed>>({});
  const [logs, setLogs] = useState<Record<string, LogEntry[]>>({});
  const [toasts, setToasts] = useState<ConsoleToast[]>([]);
  const [blockedReason, setBlockedReason] = useState<string | null>(null);

  const setupRef = useRef<ConsoleSetup | null>(null);
  const sourcesRef = useRef<Record<string, FeedSource>>({});
  const objectUrlsRef = useRef<Record<string, string>>({});
  const cameraStreamsRef = useRef<Record<string, MediaStream>>({});
  const captureRefs = useRef<Record<string, HTMLVideoElement | null>>({});
  const workersRef = useRef<Map<string, AbortController>>(new Map());
  const sessionRef = useRef<string | null>(null);
  const logSeq = useRef(0);
  const toastSeq = useRef(0);
  const toastTimers = useRef<number[]>([]);
  const feedStateRef = useRef<Record<string, { started: boolean; persons: number; posture: string; objects: string; alertId: string | null; lastPosture: number; lastHeartbeat: number }>>({});
  const verificationRef = useRef<Record<string, boolean>>({});
  const seenIncidentsRef = useRef<Record<string, boolean>>({});

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

  const toast = useCallback((tone: ConsoleToast["tone"], title: string, detail?: string) => {
    toastSeq.current += 1;
    const id = toastSeq.current;
    setToasts((prev) => [...prev, { id, tone, title, detail }].slice(-3));
    const timer = window.setTimeout(() => setToasts((prev) => prev.filter((t) => t.id !== id)), 9000);
    toastTimers.current.push(timer);
  }, []);

  const dismissToast = useCallback((id: number) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  useEffect(() => {
    const loaded = loadSetup();
    if (!loaded || !loaded.completed) {
      router.replace("/setup");
      setReady(true);
      return;
    }
    setSetup(loaded);
    setupRef.current = loaded;
    setLogs(loadLogs());
    setReady(true);
  }, [router]);

  useEffect(() => {
    setupRef.current = setup;
  }, [setup]);

  useEffect(() => {
    sourcesRef.current = sources;
  }, [sources]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      if (typeof window !== "undefined") {
        window.localStorage.setItem(LOG_KEY, JSON.stringify(logs));
      }
    }, 500);
    return () => window.clearTimeout(timer);
  }, [logs]);

  useEffect(() => () => {
    workersRef.current.forEach((controller) => controller.abort());
    const session = sessionRef.current;
    if (session) {
      void fetch("/api/wall/session/close", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ session }),
        keepalive: true,
      }).catch(() => {});
    }
    Object.values(objectUrlsRef.current).forEach((url) => URL.revokeObjectURL(url));
    Object.values(cameraStreamsRef.current).forEach((stream) => stream.getTracks().forEach((t) => t.stop()));
    toastTimers.current.forEach((timer) => window.clearTimeout(timer));
  }, []);

  // Resolve local videos and demo URLs into playable sources.
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
          if (previous) URL.revokeObjectURL(previous);
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
      setSources(next);
    })();
    return () => { cancelled = true; };
  }, [setup?.feeds]);

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
    const status = alert.verification?.status;
    if (!status) return;
    const key = `${alert.id}:${status}`;
    if (verificationRef.current[key]) return;
    verificationRef.current[key] = true;
    const reason = alert.verification?.reason || "";
    if (status === "verifying") {
      addLog(feed.id, "Model", "info", "SAM + Muse are checking the alert", reason || "No family alert has been sent yet.");
    } else if (status === "verified") {
      addLog(feed.id, "Alert", "alert", "Verified alert released", reason || "The family alert was released after corroboration.");
    } else if (status === "rejected") {
      addLog(feed.id, "Alert", "warn", "Not corroborated", reason || "No family alert was sent.");
      toast("warn", "Not corroborated", `${feed.label} · no family alert was sent.`);
    } else if (status === "unavailable") {
      addLog(feed.id, "System", "alert", "Verification unavailable", reason || "Escalated on the local floor.");
      toast("alert", "Verification unavailable", `${feed.label} · local alert escalated.`);
    }
  }, [addLog, toast]);

  const applyFrame = useCallback((feed: ConsoleFeed, data: WallFrame) => {
    setLive((prev) => ({
      ...prev,
      [feed.id]: { frame: data, status: "watching", receivedAt: Date.now() },
    }));
    const state = feedStateRef.current[feed.id] ?? {
      started: false, persons: 0, posture: "none", objects: "", alertId: null,
      lastPosture: 0, lastHeartbeat: 0,
    };
    const now = Date.now();
    const primary = data.persons[0];
    const posture = primary?.posture ?? "none";
    const objects = data.objects.map((o) => o.label).join("|");
    if (!state.started) {
      state.started = true;
      addLog(feed.id, "Vision", "good", "Tracking started",
        `${data.person_count} ${data.person_count === 1 ? "person" : "people"} · ${posture} · ${data.tracking.fps || 0} fps local`);
      state.lastHeartbeat = now;
    } else {
      if (state.persons === 0 && data.person_count > 0) {
        addLog(feed.id, "Vision", "good", "Person entered",
          `${data.person_count} ${data.person_count === 1 ? "person" : "people"}${primary?.track_id ? ` · track #${primary.track_id}` : ""}`);
      } else if (state.persons > 0 && data.person_count === 0) {
        addLog(feed.id, "Vision", "info", "Person left the frame", "Still watching the room.");
      }
      if (posture !== "none" && posture !== state.posture && now - state.lastPosture > 1200) {
        addLog(feed.id, "Vision", posture === "lying" ? "warn" : "info",
          `Posture ${posture}`,
          primary?.support_surface ? `support surface: ${primary.support_surface}` : "local pose check");
        state.lastPosture = now;
      }
      if (objects && objects !== state.objects) {
        addLog(feed.id, "Vision", "info", "Room context", data.objects.slice(0, 4).map((o) => o.label).join(", "));
      }
      if (now - state.lastHeartbeat > 20000) {
        addLog(feed.id, "Vision", "info", "Steady",
          `${data.person_count} ${data.person_count === 1 ? "person" : "people"} · ${posture} · ${data.tracking.fps || 0} fps`);
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
          what, feed.name || feed.location);
        toast(data.alert.severity === "alert" ? "alert" : "warn",
          what,
          `${feed.name || feed.label} · ${data.alert.kind === "possible_fall" ? "local alert · no family alert sent yet" : "review the feed"}`);
      }
      noteVerification(feed, data.alert);
    }
    if (data.last_alert) noteVerification(feed, data.last_alert);
    if (data.resolved) {
      addLog(feed.id, "Alert", "good", "Condition cleared", "The person is upright; recovery recorded.");
    }
  }, [addLog, noteVerification, residentName, toast]);

  const worker = useCallback(async (feed: ConsoleFeed, session: string, signal: AbortSignal) => {
    let failures = 0;
    while (!signal.aborted) {
      const video = captureRefs.current[feed.id];
      if (!video || video.readyState < 2 || !video.videoWidth) {
        await sleep(280, signal);
        continue;
      }
      if (video.paused) {
        // Activation is a user gesture; muted playback should be allowed. If
        // the browser suspended an offscreen element, nudge it back to life.
        void video.play().catch(() => {});
        await sleep(240, signal);
        if (video.paused) {
          await sleep(320, signal);
          continue;
        }
      }
      const blob = await captureFrame(video);
      if (!blob) {
        await sleep(400, signal);
        continue;
      }
      const form = new FormData();
      form.append("session", session);
      form.append("feed", JSON.stringify({ id: feed.id, name: feed.name || feed.label, kind: feed.kind }));
      form.append("frame", blob, `${feed.id}.jpg`);
      try {
        const res = await fetch("/api/wall/frame", { method: "POST", body: form, signal });
        if (!res.ok) {
          const text = await res.text();
          let detail = `analysis failed (${res.status})`;
          try { detail = (JSON.parse(text) as { detail?: string }).detail || detail; } catch { /* keep */ }
          throw new Error(detail);
        }
        const data = (await res.json()) as WallFrame;
        if (signal.aborted) return;
        failures = 0;
        applyFrame(feed, data);
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
      if (cameraStreamsRef.current[feed.id]) continue;
      try {
        const stream = await navigator.mediaDevices.getUserMedia({
          video: feed.deviceId ? { deviceId: { exact: feed.deviceId } } : true,
          audio: false,
        });
        cameraStreamsRef.current[feed.id] = stream;
        setSources((prev) => ({ ...prev, [feed.id]: { ...prev[feed.id], stream, error: undefined } }));
        addLog(feed.id, "System", "good", "Camera connected");
      } catch {
        setSources((prev) => ({ ...prev, [feed.id]: { ...prev[feed.id], error: "Camera permission is not available." } }));
        addLog(feed.id, "System", "warn", "Camera not connected", "Check the browser permission.");
      }
    }
  }, [addLog]);

  const setActive = useCallback(async (on: boolean) => {
    if (on) {
      const current = setupRef.current;
      const readiness = setupReadyToWatch(current);
      if (!readiness.ready) {
        setBlockedReason(readiness.reason);
        return;
      }
      if (!sessionRef.current) sessionRef.current = sessionId();
      setActiveState(true);
      addLogForAll("System", "good", "Watching started", "Local tracking is active.");
      try { await send("/api/privacy/resume", {}); } catch { /* local demo may not need it */ }
      await startCameras();
    } else {
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
      } catch { /* the local switch still stopped capture */ }
      addLogForAll("System", "info", "Watching paused", "No frames are being analyzed.");
      setLive({});
    }
  }, [addLogForAll, startCameras, stopCameras, stopWorkers]);

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
  }, [active, setup, startCameras]);

  // Cross-tab alerts from the real pipeline, independent of the camera wall.
  useStream((frame, payload) => {
    const event = payload as Record<string, unknown>;
    if (frame === "incident.decided") {
      const id = String(event.id || "");
      if (!id || seenIncidentsRef.current[id]) return;
      seenIncidentsRef.current[id] = true;
      const title = String(event.title || "An alert needs attention");
      const severity = String(event.severity || "watch");
      const named = title.toLowerCase().startsWith(residentName.toLowerCase())
        ? title
        : `${residentName}: ${title}`;
      toast(severity === "emergency" || severity === "urgent" ? "alert" : "warn",
        named, "Open Events for the full evidence.");
      addLog("system", "Alert", "alert", named,
        severity === "emergency" || severity === "urgent" ? "Family alert released" : "Care note");
    } else if (frame === "checkin.started") {
      toast("info", `Check-in started for ${residentName}`, "Waiting for an answer.");
      addLog("system", "System", "warn", `Check-in started for ${residentName}`);
    } else if (frame === "checkin.no_answer") {
      toast("alert", `No answer from ${residentName}`, "The check-in timed out. The alert was escalated.");
      addLog("system", "Alert", "alert", `No answer from ${residentName}`);
    } else if (frame === "checkin.answered") {
      toast("good", `${residentName} answered`, "The answer was read against the camera evidence.");
      addLog("system", "System", "good", `${residentName} answered the check-in`);
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

  const value = useMemo<ConsoleContextValue>(() => ({
    ready,
    setup,
    resident,
    active,
    setActive,
    feeds: setup?.feeds ?? [],
    updateSetup,
    sources,
    live,
    logs,
    clearLogs,
    toasts,
    dismissToast,
    blockedReason,
    clearBlocked: () => setBlockedReason(null),
  }), [ready, setup, resident, active, setActive, updateSetup, sources, live, logs, clearLogs, toasts, dismissToast, blockedReason]);

  return (
    <ConsoleContext.Provider value={value}>
      {children}
      <div aria-hidden="true" className="console-capture">
        {(setup?.feeds ?? []).map((feed) => {
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
