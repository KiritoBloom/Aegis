"use client";

import { useEffect, useRef, useState } from "react";
import type { FeedSource, WallFrame } from "@/lib/console-types";
import type { ConsoleFeed } from "@/lib/console";
import styles from "./console-home.module.css";

type Size = { w: number; h: number };

export function LiveFeed({
  feed,
  source,
  frame,
  active,
}: {
  feed: ConsoleFeed;
  source?: FeedSource;
  frame?: WallFrame;
  active: boolean;
}) {
  const stage = useRef<HTMLDivElement | null>(null);
  const video = useRef<HTMLVideoElement | null>(null);
  const [size, setSize] = useState<Size>({ w: 0, h: 0 });

  useEffect(() => {
    const el = stage.current;
    if (!el) return;
    const measure = () => setSize({ w: el.clientWidth, h: el.clientHeight });
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const el = video.current;
    if (!el) return;
    if (source?.stream) {
      el.srcObject = source.stream;
    } else {
      el.srcObject = null;
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
      if (source?.stream && el) el.srcObject = null;
    };
  }, [source?.stream, source?.url]);

  const frameW = frame?.width || 16;
  const frameH = frame?.height || 9;
  const scale = size.w && size.h
    ? Math.min(size.w / frameW, size.h / frameH)
    : 0;
  const renderW = frameW * scale;
  const renderH = frameH * scale;
  const offsetX = (size.w - renderW) / 2;
  const offsetY = (size.h - renderH) / 2;

  const show = active && Boolean(frame);
  const persons = show && frame ? frame.persons : [];
  const objects = show && frame
    ? frame.objects.filter((item) => item.label !== "person").slice(0, 5)
    : [];

  return (
    <div className={styles.stage} ref={stage}>
      <video
        ref={video}
        className={styles.tileVideo}
        src={source?.stream ? undefined : source?.url}
        autoPlay
        muted
        loop={feed.kind !== "camera" && feed.replay}
        playsInline
      />
      {scale > 0 ? (
        <div className={styles.detections} aria-hidden="true">
          {objects.map((object, index) => {
            const [x1, y1, x2, y2] = object.box;
            if (x2 <= x1 || y2 <= y1) return null;
            return (
              <span
                key={`${object.label}-${index}`}
                className={styles.objectBox}
                style={{
                  left: offsetX + x1 * renderW,
                  top: offsetY + y1 * renderH,
                  width: (x2 - x1) * renderW,
                  height: (y2 - y1) * renderH,
                }}
              >
                <span className={styles.objectTag}>{object.label}</span>
              </span>
            );
          })}
          {persons.map((person, index) => {
            const [x1, y1, x2, y2] = person.box;
            if (x2 <= x1 || y2 <= y1) return null;
            return (
              <span
                key={`${person.track_id ?? "p"}-${index}`}
                className={styles.personBox}
                style={{
                  left: offsetX + x1 * renderW,
                  top: offsetY + y1 * renderH,
                  width: (x2 - x1) * renderW,
                  height: (y2 - y1) * renderH,
                }}
              >
                <span className={styles.personTag}>
                  {person.track_id ? `TRK ${person.track_id}` : `PERSON ${index + 1}`}
                  {" · "}
                  {person.posture}
                </span>
              </span>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}
