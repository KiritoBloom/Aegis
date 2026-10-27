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

/**
 * One GET, one poll, one error surface.
 *
 * `poll` defaults to 15s, which is the interval at which the dashboard still
 * feels live without turning a care app into a load generator on a phone. Live
 * views pass a shorter interval; the alert list subscribes to SSE instead.
 */
export function useApi<T>(path: string, poll = 15000): Loadable<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);
  const alive = useRef(true);

  const reload = useCallback(() => setTick((n) => n + 1), []);

  useEffect(() => {
    alive.current = true;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const run = async () => {
      try {
        const next = await get<T>(path);
        if (!alive.current) return;
        setData(next);
        setError(null);
      } catch (err) {
        if (!alive.current) return;
        setError(err instanceof Error ? err.message : "Could not reach the house");
      } finally {
        if (alive.current) {
          setLoading(false);
          if (poll > 0) timer = setTimeout(run, poll);
        }
      }
    };

    run();
    return () => {
      alive.current = false;
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

/**
 * SSE as an upgrade, never a dependency.
 *
 * The backend emits *named* frames (`incident.created`, `incident.notified`, …)
 * and an unnamed `snapshot` on connect, so a bare `onmessage` handler would sit
 * there receiving nothing. Both are wired, and the handler is told which frame
 * arrived so a page can decide what is worth re-fetching.
 *
 * If the stream dies the page keeps its last good data and the poll above
 * catches up; the reconnect is silent because a red toast about a websocket
 * would be the loudest thing on a quiet night.
 */
const FRAMES = [
  "incident.created",
  "incident.clip",
  "incident.decided",
  "incident.notified",
  "incident.suppressed",
  "incident.escalated",
  "incident.ack",
  "watch.updated",
  "privacy.paused",
  "privacy.resumed",
  "privacy.consent",
  "privacy.deleted",
  "household.updated",
] as const;

export function useStream(onEvent: (frame: string, payload: unknown) => void) {
  const handler = useRef(onEvent);
  handler.current = onEvent;

  useEffect(() => {
    let source: EventSource | null = null;
    let retry: ReturnType<typeof setTimeout> | null = null;

    const connect = () => {
      source = new EventSource("/api/events/stream");
      const dispatch = (frame: string) => (ev: MessageEvent) => {
        try {
          handler.current(frame, JSON.parse(ev.data));
        } catch {
          /* a keepalive comment or a frame we do not understand */
        }
      };
      source.onmessage = dispatch("message");
      source.addEventListener("snapshot", dispatch("snapshot") as EventListener);
      for (const frame of FRAMES) {
        source.addEventListener(frame, dispatch(frame) as EventListener);
      }
      source.onerror = () => {
        source?.close();
        retry = setTimeout(connect, 5000);
      };
    };

    connect();
    return () => {
      source?.close();
      if (retry) clearTimeout(retry);
    };
  }, []);
}
