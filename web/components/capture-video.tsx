"use client";

import { useEffect, useRef } from "react";
import type { FeedSource } from "@/lib/console-types";
import type { ConsoleFeed } from "@/lib/console";

export function CaptureVideo({
  feed,
  source,
  register,
}: {
  feed: ConsoleFeed;
  source: FeedSource;
  register: (el: HTMLVideoElement | null) => void;
}) {
  const ref = useRef<HTMLVideoElement | null>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (source.stream) {
      el.srcObject = source.stream;
    }
    const attempt = () => {
      if (el.paused) void el.play().catch(() => {});
    };
    attempt();
    el.addEventListener("loadeddata", attempt);
    el.addEventListener("canplay", attempt);
    return () => {
      el.removeEventListener("loadeddata", attempt);
      el.removeEventListener("canplay", attempt);
      if (source.stream) el.srcObject = null;
    };
  }, [source.stream, source.url]);
  return (
    <video
      ref={(el) => {
        ref.current = el;
        register(el);
      }}
      src={source.stream ? undefined : source.url}
      autoPlay
      muted
      loop={feed.kind !== "camera" && feed.replay}
      playsInline
    />
  );
}
