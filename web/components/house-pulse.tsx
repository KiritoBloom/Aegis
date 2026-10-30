"use client";

import Link from "next/link";
import { Icon } from "@/components/icons";
import { ago, type Camera, type Household } from "@/lib/api";
import { useApi, useNow } from "@/lib/useApi";

/** Persistent header status for the house. */
export function HousePulse() {
  const camera = useApi<Camera>("/api/camera", 15000);
  const household = useApi<Household>("/api/household", 30000);
  const now = useNow(30000);

  const paused = household.data?.privacy.pause.paused;
  const online = camera.data?.camera.online;
  const state = paused ? "paused" : online === false ? "offline" : "quiet";
  const label = paused
    ? "monitoring paused"
    : camera.data === null
      ? "checking the house"
      : online
        ? "camera online"
        : "camera offline";
  const heart = camera.data?.camera.last_seen ? ago(camera.data.camera.last_seen) : "no heartbeat yet";
  const date = new Date(now).toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" });
  const time = new Date(now).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", hour12: false });

  return (
    <div className="house-pulse">
      <span className="pulse-state" data-state={state} aria-live="polite">
        <span className="pulse-dot" aria-hidden="true" />
        {label}
        <span className="pulse-heart" style={{ color: "var(--color-ink-3)" }}>· {heart}</span>
      </span>
      <span className="pulse-sep" aria-hidden="true" />
      <span className="pulse-clock" suppressHydrationWarning>{date} · {time}</span>
      <Link href="/night" className="pulse-night">
        <Icon name="night" size={13} />
        Night
      </Link>
    </div>
  );
}
