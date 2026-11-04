const canvases = new WeakMap<HTMLVideoElement, HTMLCanvasElement>();

/** The displayed picture is decoded from the exact JPEG submitted for analysis. */
export async function captureObservation(video: HTMLVideoElement) {
  const mediaTime = video.currentTime;
  const ended = video.ended && !video.loop;
  const blob = await captureFrame(video);
  if (!blob) return null;
  const url = URL.createObjectURL(blob);
  const image = new Image();
  try {
    image.src = url;
    await image.decode();
    return { blob, image, mediaTime, ended };
  } finally {
    URL.revokeObjectURL(url);
  }
}

export function captureFrame(video: HTMLVideoElement): Promise<Blob | null> {
  if (!video.videoWidth || !video.videoHeight) return Promise.resolve(null);
  const scale = Math.min(1, 768 / video.videoWidth);
  const canvas = canvases.get(video) ?? document.createElement("canvas");
  canvases.set(video, canvas);
  const width = Math.max(1, Math.round(video.videoWidth * scale));
  const height = Math.max(1, Math.round(video.videoHeight * scale));
  if (canvas.width !== width) canvas.width = width;
  if (canvas.height !== height) canvas.height = height;
  const ctx = canvas.getContext("2d");
  if (!ctx) return Promise.resolve(null);
  ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
  return new Promise((resolve) => canvas.toBlob((blob) => resolve(blob), "image/jpeg", 0.82));
}

export function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const finish = () => {
      window.clearTimeout(timer);
      signal?.removeEventListener("abort", finish);
      resolve();
    };
    const timer = window.setTimeout(finish, ms);
    signal?.addEventListener("abort", finish, { once: true });
    if (signal?.aborted) finish();
  });
}
