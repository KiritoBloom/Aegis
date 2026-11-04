"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { get } from "./api";

export type Loadable<T> = {
  data: T | null;
  error: string | null;
  loading: boolean;
  reload: () => void;
  setData: (value: T | null) => void;
};

/** One GET, one poll, one error surface. */
export function useApi<T>(path: string, poll = 15000): Loadable<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);

  const reload = useCallback(() => setTick((n) => n + 1), []);

  useEffect(() => { setData(null); setError(null); }, [path]);

  useEffect(() => {
    let alive = true;
    const controller = new AbortController();
    setLoading(true);
    let timer: ReturnType<typeof setTimeout> | null = null;

    const run = async () => {
      try {
        const next = await get<T>(path, controller.signal);
        if (!alive) return;
        setData(next);
        setError(null);
      } catch (err) {
        if (!alive) return;
        setError(err instanceof Error ? err.message : "Could not reach the house");
      } finally {
        if (alive) {
          setLoading(false);
          if (poll > 0) timer = setTimeout(run, poll);
        }
      }
    };

    run();
    return () => {
      alive = false;
      controller.abort();
      if (timer) clearTimeout(timer);
    };
  }, [path, poll, tick]);

  return { data, error, loading, reload, setData };
}

/** A relative-time tick, so "just now" does not lie for ten minutes. */
export function useNow(interval = 30000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), interval);
    return () => clearInterval(t);
  }, [interval]);
  return now;
}

/** SSE is an upgrade; every view still works from a single GET. */
const FRAMES = [
  "incident.created",
  "incident.clip",
  "incident.decided",
  "incident.notified",
  "incident.suppressed",
  "incident.escalated",
  "incident.ack",
  "incident.recovered",
  "checkin.started",
  "checkin.reopened",
  "pipeline.step",
  "checkin.answered",
  "checkin.no_answer",
  "checkin.cancelled",
  "context.read",
  "visitor.expected",
  "night.note",
  "watch.updated",
  "privacy.paused",
  "privacy.resumed",
  "privacy.consent",
  "privacy.deleted",
  "household.updated",
  "wall.verification",
] as const;

// One stream per browser tab. A stream per component exhausted HTTP/1's six
// connections and left navigation and frame uploads waiting behind idle SSEs.
const listeners = new Set<(frame: string, payload: unknown) => void>();
let sharedStream: EventSource | null = null;

function openStream() {
  if (sharedStream) return;
  sharedStream = new EventSource("/api/events/stream");
  const dispatch = (frame: string) => (event: MessageEvent) => {
    let payload: unknown;
    try { payload = JSON.parse(event.data); } catch { return; }
    for (const listener of listeners) listener(frame, payload);
  };
  sharedStream.onmessage = dispatch("message");
  for (const frame of ["snapshot", ...FRAMES]) {
    sharedStream.addEventListener(frame, dispatch(frame) as EventListener);
  }
  // EventSource owns reconnects and backoff; do not create competing timers.
}

export function useStream(onEvent: (frame: string, payload: unknown) => void) {
  const handler = useRef(onEvent);
  handler.current = onEvent;

  useEffect(() => {
    const listener = (frame: string, payload: unknown) => handler.current(frame, payload);
    listeners.add(listener);
    openStream();
    return () => {
      listeners.delete(listener);
      if (!listeners.size) {
        sharedStream?.close();
        sharedStream = null;
      }
    };
  }, []);
}
