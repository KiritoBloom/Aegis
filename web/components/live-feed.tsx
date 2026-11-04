"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import type { FeedSource, WallFrame } from "@/lib/console-types";
import type { ConsoleFeed } from "@/lib/console";
import { useConsole } from "./console-provider";
import { useNow } from "@/lib/useApi";
import styles from "./console-home.module.css";

export function LiveFeed({ feed, source, frame, active, detailed = false }: {
  feed: ConsoleFeed; source?: FeedSource; frame?: WallFrame; active: boolean; detailed?: boolean;
}) {
  const stage = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const [size, setSize] = useState({ w: 0, h: 0 });
  const { live } = useConsole();
  const now = useNow(500);
  const state = live[feed.id];
  const observation = state?.observation;

  // Picture and detections commit together. There is no second player/clock to
  // drift, restart on remount, or show new pixels underneath old keypoints.
  useLayoutEffect(() => {
    const el = canvas.current;
    if (!el || !observation) return;
    el.width = observation.image.naturalWidth;
    el.height = observation.image.naturalHeight;
    el.getContext("2d")?.drawImage(observation.image, 0, 0);
  }, [observation]);

  useEffect(() => {
    const el = stage.current;
    if (!el) return;
    const measure = () => setSize({ w: el.clientWidth, h: el.clientHeight });
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const frameW = observation?.image.naturalWidth || frame?.width || 16;
  const frameH = observation?.image.naturalHeight || frame?.height || 9;
  const scale = Math.min(size.w / frameW, size.h / frameH);
  const renderW = frameW * scale, renderH = frameH * scale;
  const offsetX = (size.w - renderW) / 2, offsetY = (size.h - renderH) / 2;
  const stale = state?.status !== "ended" && now - (state?.receivedAt ?? now) > 2000;
  const persons = active && observation && frame && !stale ? frame.persons : [];
  const bones = [[0, 1], [0, 2], [1, 3], [2, 4], [3, 5], [4, 6], [5, 6], [5, 7], [7, 9], [6, 8], [8, 10], [5, 11], [6, 12], [11, 12], [11, 13], [13, 15], [12, 14], [14, 16]];
  const postureLabel: Record<string, string> = { upright: "Upright", lying: "Lying down", sitting: "Seated", bending: "Bending", kneeling: "Kneeling", unknown: "Position unclear" };

  return <div className={styles.stage} ref={stage}>
    <canvas ref={canvas} className={styles.tileVideo} role="img" aria-label={`${feed.name || feed.label} · ${state?.status === "ended" ? "final recording frame" : "camera observation"}`}
      data-media-time={observation?.mediaTime} data-ended={observation?.ended || false} />
    {!observation ? <div className={styles.tileStandby}><p>{source?.error || "Opening the camera view…"}</p></div> : null}
    {scale > 0 && persons.length ? <div className={styles.detections} aria-hidden="true">
      {detailed ? <svg className={styles.skeleton} style={{ left: offsetX, top: offsetY, width: renderW, height: renderH }} viewBox={`0 0 ${frameW} ${frameH}`}>
        {persons.map((person, index) => <g key={person.track_id ?? index} data-down={person.posture === "lying"}>
          {bones.map(([a, b]) => {
            const p = person.skeleton?.[a], q = person.skeleton?.[b];
            return p && q && p[2] >= .5 && q[2] >= .5 ? <line key={`${a}-${b}`} x1={p[0] * frameW} y1={p[1] * frameH} x2={q[0] * frameW} y2={q[1] * frameH} /> : null;
          })}
          {person.skeleton?.map((point, joint) => point[2] >= .5
            ? <circle key={joint} cx={point[0] * frameW} cy={point[1] * frameH} r={2.4 / scale} /> : null)}
        </g>)}
      </svg> : null}
      {persons.map((person, index) => {
        const [x1, y1, x2, y2] = person.box;
        return <span key={person.track_id ?? index} className={styles.personBox} data-down={person.posture === "lying"}
          style={{ left: offsetX + x1 * renderW, top: offsetY + y1 * renderH, width: (x2 - x1) * renderW, height: (y2 - y1) * renderH }}>
          <span className={styles.personTag}>{persons.length > 1 ? `Person ${index + 1} · ` : ""}{postureLabel[person.posture] ?? "Person detected"}{person.support_surface ? ` · on ${person.support_surface}` : ""}</span>
        </span>;
      })}
    </div> : null}
    {active && stale ? <span className={styles.staleOverlay}>Connection delayed · showing the last received image</span> : null}
  </div>;
}
