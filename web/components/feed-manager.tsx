"use client";

import { useEffect, useRef, useState } from "react";
import {
  MAX_CONSOLE_FEEDS,
  ROOM_SUGGESTIONS,
  deleteFeedMedia,
  feedLabelFor,
  newId,
  putFeedMedia,
  type ConsoleFeed,
} from "@/lib/console";
import styles from "./feed-manager.module.css";

type DemoVideo = { name: string; url: string; bytes?: number };
type Device = { deviceId: string; label: string };

export function FeedManager({
  feeds,
  onChange,
  tone = "light",
  limit = MAX_CONSOLE_FEEDS,
}: {
  feeds: ConsoleFeed[];
  onChange: (feeds: ConsoleFeed[]) => void;
  tone?: "light" | "dark";
  limit?: number;
}) {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [devices, setDevices] = useState<Device[] | null>(null);
  const [demos, setDemos] = useState<DemoVideo[] | null>(null);
  const fileRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    if (!devices && !demos) return;
    const close = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setDevices(null);
        setDemos(null);
      }
    };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [devices, demos]);

  const patch = (id: string, next: Partial<ConsoleFeed>) => {
    onChange(feeds.map((feed) => (feed.id === id ? { ...feed, ...next } : feed)));
  };

  const addFeed = (feed: ConsoleFeed) => {
    if (feeds.length >= limit) {
      setError(`This console holds ${limit} cameras. Remove one before adding another.`);
      return;
    }
    onChange([...feeds, feed]);
  };

  const onFiles = async (files: FileList | null) => {
    if (!files?.length) return;
    setError(null);
    const room = Math.max(0, limit - feeds.length);
    const chosen = Array.from(files).filter((f) => f.type.startsWith("video/")).slice(0, room);
    if (!chosen.length) {
      setError("Choose a video file (mp4, webm, or mov).");
      return;
    }
    setBusy("Storing the recording in this browser…");
    try {
      const added: ConsoleFeed[] = [];
      for (const file of chosen) {
        const id = newId("feed");
        await putFeedMedia(id, file);
        added.push({
          id,
          label: feedLabelFor(feeds.length + added.length),
          name: file.name.replace(/\.[^.]+$/, ""),
          location: "",
          kind: "file",
          replay: true,
          addedAt: new Date().toISOString(),
        });
      }
      addFeedList(added);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not store that video on this device.");
    } finally {
      setBusy(null);
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  const addFeedList = (list: ConsoleFeed[]) => {
    if (!list.length) return;
    const room = Math.max(0, limit - feeds.length);
    onChange([...feeds, ...list.slice(0, room)]);
    if (list.length > room) setError(`Only ${room} more camera${room === 1 ? "" : "s"} could be added.`);
  };

  const openDevices = async () => {
    setError(null);
    setDemos(null);
    if (!navigator.mediaDevices?.getUserMedia) {
      setError("This browser cannot open a camera.");
      return;
    }
    setBusy("Checking camera permission…");
    try {
      const permission = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
      permission.getTracks().forEach((track) => track.stop());
      const found = (await navigator.mediaDevices.enumerateDevices())
        .filter((d) => d.kind === "videoinput");
      if (!found.length) {
        setError("No camera was found on this device.");
        return;
      }
      setDevices(found.map((d, i) => ({
        deviceId: d.deviceId,
        label: d.label || `Device camera ${i + 1}`,
      })));
    } catch {
      setError("Camera permission was not granted. The browser asked; it was declined or blocked.");
    } finally {
      setBusy(null);
    }
  };

  const addDevice = (deviceId: string, label: string) => {
    addFeed({
      id: newId("feed"),
      label: feedLabelFor(feeds.length),
      name: label,
      location: "",
      kind: "camera",
      replay: false,
      deviceId,
      addedAt: new Date().toISOString(),
    });
    setDevices(null);
  };

  const openDemos = async () => {
    setError(null);
    setDevices(null);
    setBusy("Looking for lab footage…");
    try {
      const res = await fetch("/api/demo/videos", { cache: "no-store" });
      if (!res.ok) throw new Error(`The server answered ${res.status}`);
      const data = (await res.json()) as { items?: DemoVideo[] };
      const items = data.items ?? [];
      if (!items.length) {
        setError("No lab footage is configured on the server. Add a local recording or a device camera.");
        return;
      }
      setDemos(items);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not list the lab footage.");
    } finally {
      setBusy(null);
    }
  };

  const addDemo = (item: DemoVideo) => {
    addFeed({
      id: newId("feed"),
      label: feedLabelFor(feeds.length),
      name: item.name.replace(/\.[^.]+$/, "").replace(/[-_]/g, " "),
      location: "",
      kind: "demo",
      replay: true,
      demoUrl: item.url,
      addedAt: new Date().toISOString(),
    });
    setDemos(null);
  };

  const remove = (feed: ConsoleFeed) => {
    onChange(feeds.filter((f) => f.id !== feed.id));
    if (feed.kind === "file") void deleteFeedMedia(feed.id);
  };

  return (
    <div className={styles.root} data-tone={tone}>
      <div className={styles.actions}>
        <input
          ref={fileRef}
          className={styles.fileInput}
          type="file"
          accept="video/*"
          multiple
          onChange={(e) => void onFiles(e.target.files)}
        />
        <button type="button" className={styles.add} onClick={() => fileRef.current?.click()}
          disabled={feeds.length >= limit || Boolean(busy)}>
          Add a recording
        </button>
        <button type="button" className={styles.add} onClick={() => void openDevices()}
          disabled={feeds.length >= limit || Boolean(busy)}>
          Use a device camera
        </button>
        <button type="button" className={styles.add} onClick={() => void openDemos()}
          disabled={feeds.length >= limit || Boolean(busy)}>
          Add lab footage
        </button>
      </div>

      {busy ? <p className={styles.busy}>{busy}</p> : null}
      {error ? <p className={styles.error} role="alert">{error}</p> : null}

      {devices ? (
        <div className={styles.menu} role="dialog" aria-label="Choose a camera">
          <div className={styles.menuHead}>
            <span>Device cameras</span>
            <button type="button" className={styles.close} onClick={() => setDevices(null)} aria-label="Close">×</button>
          </div>
          {devices.map((device) => (
            <button key={device.deviceId} type="button" className={styles.menuRow}
              onClick={() => addDevice(device.deviceId, device.label)}>
              <span>{device.label}</span>
              <span className={styles.menuMeta}>video input</span>
            </button>
          ))}
          <p className={styles.menuNote}>The browser may ask for permission each time the console starts.</p>
        </div>
      ) : null}

      {demos ? (
        <div className={styles.menu} role="dialog" aria-label="Choose lab footage">
          <div className={styles.menuHead}>
            <span>Lab footage</span>
            <button type="button" className={styles.close} onClick={() => setDemos(null)} aria-label="Close">×</button>
          </div>
          {demos.map((item) => (
            <button key={item.name} type="button" className={styles.menuRow} onClick={() => addDemo(item)}>
              <span>{item.name.replace(/\.[^.]+$/, "").replace(/[-_]/g, " ")}</span>
              <span className={styles.menuMeta}>replay</span>
            </button>
          ))}
          <p className={styles.menuNote}>Footage is served by the local backend. Frames are analyzed on this machine.</p>
        </div>
      ) : null}

      {feeds.length ? (
        <div className={styles.grid}>
          {feeds.map((feed, index) => (
            <div key={feed.id} className={styles.card} data-kind={feed.kind}>
              <div className={styles.cardTop}>
                <span className={styles.index}>CAM {String(index + 1).padStart(2, "0")}</span>
                <span className={styles.kind}>{feed.kind === "camera" ? "device" : feed.kind}</span>
                <button type="button" className={styles.remove} onClick={() => remove(feed)}
                  aria-label={`Remove ${feed.name || feed.label}`}>×</button>
              </div>
              <label className={styles.field}>
                <span>Display name</span>
                <input
                  value={feed.name}
                  onChange={(e) => patch(feed.id, { name: e.target.value })}
                  placeholder={feed.label}
                  autoComplete="off"
                />
              </label>
              <label className={styles.field}>
                <span>Where is it pointing?</span>
                <input
                  value={feed.location}
                  onChange={(e) => patch(feed.id, { location: e.target.value })}
                  placeholder="Living room"
                  list={`rooms-${feed.id}`}
                  autoComplete="off"
                />
                <datalist id={`rooms-${feed.id}`}>
                  {ROOM_SUGGESTIONS.map((room) => <option key={room} value={room} />)}
                </datalist>
              </label>
              <div className={styles.cardMeta}>
                <span>
                  {feed.kind === "file" ? "Stored in this browser"
                    : feed.kind === "camera" ? "Live device feed"
                      : "Local replay"}
                </span>
                {feed.kind !== "camera" ? (
                  <label className={styles.check}>
                    <input type="checkbox" checked={feed.replay}
                      onChange={(e) => patch(feed.id, { replay: e.target.checked })} />
                    Loop
                  </label>
                ) : null}
              </div>
            </div>
          ))}
        </div>
      ) : (
        <p className={styles.empty}>
          No camera yet. A recording, a device camera, or one of the lab clips is enough to start.
        </p>
      )}
    </div>
  );
}
