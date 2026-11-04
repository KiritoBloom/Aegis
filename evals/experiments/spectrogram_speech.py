"""Bounded, real-API feasibility test: spectrogram masks for overlapping speech.

Run with the submitted Python environment. Audio stays in ignored .tmp; report
and exact model responses record failures as well as successes. This is not an
alerting feature or a benchmark of real television/room acoustics.
"""
from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import html
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import wave

import cv2
import httpx
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "scripts")]
from app.meta import MetaClient, output_text, parse_sam
from evidence_stamp import stamp
from serve import load_env

OUT = ROOT / ".tmp" / "spectrogram-speech"
SR, FFT, HOP = 16000, 1024, 128
WINDOW = np.hanning(FFT)
WIDTH, HEIGHT, MAX_HZ = 1600, 768, 4000
ROWS_URL = "https://datasets-server.huggingface.co/rows?dataset=openslr%2Flibrispeech_asr&config=clean&split=test&offset=1&length=1"
BACKGROUND_URL = "https://huggingface.co/datasets/podscripter-project/test-fixtures/resolve/main/en/fleurs_en_test_18066939864018796519.wav"


def save_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def download(url, path):
    if not path.exists():
        response = httpx.get(url, follow_redirects=True, timeout=90)
        response.raise_for_status()
        path.write_bytes(response.content)


def read_wav(path):
    # Hub previews may serve FLAC despite the local filename; decode the actual container.
    decoded = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "f64le", "-ac", "1",
                              "-ar", str(SR), "pipe:1"], capture_output=True, check=True, timeout=60)
    return np.frombuffer(decoded.stdout, "<f8").copy()


def write_wav(path, audio):
    audio = audio / max(np.max(np.abs(audio)) / .95, 1e-8)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SR)
        wav.writeframes((np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes())


def stft(audio):
    padded = np.pad(audio, (FFT // 2, FFT + HOP))
    frames = np.lib.stride_tricks.sliding_window_view(padded, FFT)[::HOP]
    return np.fft.rfft(frames * WINDOW, axis=1).T


def istft(spectrum, length):
    frames = np.fft.irfft(spectrum.T, n=FFT, axis=1) * WINDOW
    result = np.zeros((len(frames) - 1) * HOP + FFT)
    norm = np.zeros_like(result)
    for index, frame in enumerate(frames):
        start = index * HOP
        result[start:start + FFT] += frame
        norm[start:start + FFT] += WINDOW ** 2
    result /= np.maximum(norm, 1e-12)
    return result[FFT // 2:FFT // 2 + length]


def spectrogram(spectrum):
    bins = int(MAX_HZ / (SR / FFT)) + 1
    db = 20 * np.log10(np.abs(spectrum[:bins]) + 1e-9)
    gray = np.clip((db - db.max() + 65) / 65 * 255, 0, 255).astype(np.uint8)
    return cv2.applyColorMap(cv2.resize(np.flipud(gray), (WIDTH, HEIGHT)), cv2.COLORMAP_MAGMA)


def image_mask_to_spectrum(mask, shape):
    bins = int(MAX_HZ / (SR / FFT)) + 1
    result = np.zeros(shape)
    result[:bins] = cv2.resize(np.flipud(mask.astype(float)), (shape[1], bins), interpolation=cv2.INTER_LINEAR)
    return np.clip(result, 0, 1)


def si_sdr(audio, target):
    audio, target = audio - audio.mean(), target - target.mean()
    projection = np.dot(audio, target) / (np.dot(target, target) + 1e-12) * target
    return round(float(10 * np.log10((np.dot(projection, projection) + 1e-12) /
                                    (np.sum((audio - projection) ** 2) + 1e-12))), 3)


def wer(reference, hypothesis):
    def words(text):
        return re.findall(r"[a-z0-9]+", text.lower())
    ref, hyp = words(reference), words(hypothesis)
    previous = list(range(len(hyp) + 1))
    for i, word in enumerate(ref, 1):
        current = [i]
        for j, other in enumerate(hyp, 1):
            current.append(min(current[-1] + 1, previous[j] + 1, previous[j - 1] + (word != other)))
        previous = current
    return round(previous[-1] / max(len(ref), 1), 4)


def model_request(name, payload):
    key = digest(json.dumps(payload, sort_keys=True).encode())
    path = OUT / f"{name}.response.json"
    if path.exists():
        prior = json.loads(path.read_text(encoding="utf-8"))
        if prior.get("request_sha256") == key:
            return prior
    client = MetaClient(os.environ["META_API_KEY"], timeout=90)
    started = time.monotonic()
    record = {"request_sha256": key, "model": payload["model"]}
    try:
        record["response"] = client._post("/responses", payload, retries=0)
    except Exception as exc:
        record["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        client.close()
    record["latency_s"] = round(time.monotonic() - started, 3)
    save_json(path, record)
    return record


def image_part(path):
    return {"type": "input_image", "image_url": "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode()}


def vision(name, prompt, paths, schema):
    payload = {"model": "muse-spark-1.3", "reasoning": {"effort": "low"}, "max_output_tokens": 3500,
               "input": [{"type": "message", "role": "user", "content": [
                   {"type": "input_text", "text": prompt}, *[image_part(path) for path in paths]]}],
               "text": {"format": {"type": "json_schema", "name": "spectrogram", "schema": schema, "strict": True}}}
    record = model_request(name, payload)
    record["prompt"] = prompt
    record["images"] = [path.name for path in paths]
    try:
        record["parsed"] = json.loads(output_text(record.get("response", {})))
    except ValueError:
        record["parsed"] = {}
    return record


def transcribe(path):
    cache = path.with_suffix(".asr.json")
    key = digest(path.read_bytes())
    if cache.exists():
        prior = json.loads(cache.read_text(encoding="utf-8"))
        if prior.get("audio_sha256") == key:
            return prior
    client = MetaClient(os.environ["META_API_KEY"], timeout=90)
    started = time.monotonic()
    result = {"audio_sha256": key, "model": "muse-voice-transcribe-1.0"}
    try:
        result["transcript"] = client.transcribe(path, "aegis-spectrogram-" + path.stem)
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        client.close()
    result["latency_s"] = round(time.monotonic() - started, 3)
    save_json(cache, result)
    return result


def sam(name, prompt, path):
    record = model_request(name, {
        "model": "sam-3.1", "reasoning": {"effort": "low"}, "metadata": {"mask_encoding": "one_bit"},
        "input": [{"type": "message", "role": "user", "content": [
            {"type": "input_text", "text": prompt}, image_part(path)]}],
    })
    objects = parse_sam(output_text(record.get("response", {})))
    masks = []
    for obj in objects:
        raster = obj.raster()
        if not raster:
            continue
        mw, mh, bits = raster
        crop = np.frombuffer(bits, np.uint8).reshape(mh, mw)
        x1, y1, x2, y2 = [int(round(v)) for v in (obj.x1, obj.y1, obj.x2, obj.y2)]
        if x2 <= x1 or y2 <= y1:
            continue
        canvas = np.zeros((int(obj.img_h), int(obj.img_w)), np.uint8)
        crop = cv2.resize(crop, (x2 - x1, y2 - y1), interpolation=cv2.INTER_NEAREST)
        left, top, right, bottom = max(x1, 0), max(y1, 0), min(x2, canvas.shape[1]), min(y2, canvas.shape[0])
        canvas[top:bottom, left:right] = crop[top - y1:bottom - y1, left - x1:right - x1]
        masks.append(cv2.resize(canvas, (WIDTH, HEIGHT), interpolation=cv2.INTER_NEAREST))
    return {"prompt": prompt, "object_count": len(objects), "decoded_masks": len(masks), "call": record}, masks


def run_case(target, background, snr_db, reference, pool):
    name = f"minus{abs(snr_db)}db"
    delay = 2 * SR
    interference = np.zeros(len(target))
    interference[delay:] = np.resize(background, len(target) - delay)
    factor = np.sqrt(np.mean(target[delay:] ** 2) / np.mean(interference[delay:] ** 2)) * 10 ** (-snr_db / 20)
    interference *= factor
    mixture = target + interference
    target_spec, noise_spec, mix_spec = stft(target), stft(interference), stft(mixture)
    assert np.max(np.abs(istft(mix_spec, len(mixture)) - mixture)) < 1e-9, "STFT roundtrip failed"
    image_path = OUT / f"{name}-spectrogram.png"
    image = spectrogram(mix_spec)
    cv2.imwrite(str(image_path), image)
    mix_path = OUT / f"{name}-mixture.wav"
    write_wav(mix_path, mixture)
    raw_future = pool.submit(transcribe, mix_path)
    duration = len(target) / SR
    time_extent = (mix_spec.shape[1] - 1) * HOP / SR
    prompt = (
        "This is an actual audio magnitude spectrogram, not a photograph. We are TESTING whether visual models can separate "
        "overlapping voices. Horizontal axis runs from 0 to %.3f seconds, vertical from 4000 Hz at top to 0 Hz at bottom. "
        "The TARGET voice is the only speaker in the first 2 seconds and continues afterwards; a different, louder voice begins "
        "at 2 seconds. No words or source transcript are provided. Follow the target's harmonic spacing and continuity if visible. "
        "Describe a short concrete VISUAL noun phrase (max 12 words) for SAM image segmentation to select target-associated "
        "ridges; do not assume SAM understands who the resident is. Also identify up to 16 time/frequency rectangles containing "
        "target energy that might attenuate the competing voice, using normalized image coordinates x1,y1,x2,y2. "
        "Do not include the first 2 seconds in rectangles, as that interval will pass through unchanged. "
        "If separating speakers is not visually supported, say so and return no rectangles. No guessing words."
    ) % time_extent
    schema = {"type": "object", "properties": {
        "separable": {"type": "boolean"}, "observation": {"type": "string"}, "sam_prompt": {"type": "string"},
        "regions": {"type": "array", "items": {"type": "object", "properties": {
            k: {"type": "number"} for k in ("x1", "y1", "x2", "y2")},
            "required": ["x1", "y1", "x2", "y2"], "additionalProperties": False}}},
        "required": ["separable", "observation", "sam_prompt", "regions"], "additionalProperties": False}
    plan = vision(name + "-plan", prompt, [image_path], schema)
    print(name, "Muse plan:", plan["parsed"], flush=True)
    prompts = ["harmonic speech bands", "horizontal bright ridges"]
    proposed = plan["parsed"].get("sam_prompt", "").strip()
    if proposed and proposed not in prompts:
        prompts.append(proposed)
    futures = [pool.submit(sam, f"{name}-sam-{i}", p, image_path) for i, p in enumerate(prompts)]
    records, masks = [], []
    for future in futures:
        record, found = future.result()
        record["mask_ids"] = list(range(len(masks), len(masks) + len(found)))
        records.append(record)
        masks.extend(found)
    print(name, "SAM mask counts:", [(r["prompt"], r["decoded_masks"]) for r in records], flush=True)
    selection = None
    picked = []
    if masks:
        atlas = []
        for start in range(0, min(len(masks), 32), 8):
            tiles = []
            for index in range(start, min(start + 8, len(masks), 32)):
                preview = image.copy()
                preview[masks[index] > 0] = (.3 * preview[masks[index] > 0] + .7 * np.array([0, 255, 0])).astype(np.uint8)
                preview = cv2.resize(preview, (800, 384))
                cv2.putText(preview, f"MASK {index}", (12, 32), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
                tiles.append(preview)
            atlas_path = OUT / f"{name}-masks-{start}.png"
            cv2.imwrite(str(atlas_path), np.vstack(tiles))
            atlas.append(atlas_path)
        selection = vision(name + "-selection",
            "The first image is a mixed-speech spectrogram: target voice alone 0-2 seconds, then louder interfering voice. "
            f"Horizontal extent {time_extent:.3f} seconds; vertical 4000 to 0 Hz. Remaining images show candidate SAM masks "
            "in green, labelled MASK id. Select only masks that isolate the TARGET's harmonic structures after 2 seconds. "
            "A mask covering the whole image or both speakers is not separation. Select none if none actually separate. "
            "Do not select using transcript or expected words; none have been supplied.", [image_path, *atlas],
            {"type": "object", "properties": {"selected_ids": {"type": "array", "items": {"type": "integer"}},
                "observation": {"type": "string"}}, "required": ["selected_ids", "observation"], "additionalProperties": False})
        picked = [i for i in selection["parsed"].get("selected_ids", []) if isinstance(i, int) and 0 <= i < len(masks)]
    filters = {}
    freqs = np.arange(mix_spec.shape[0]) * SR / FFT
    filters["bandpass"] = np.broadcast_to(((freqs >= 300) & (freqs <= 3400))[:, None], mix_spec.shape).astype(float).copy()
    # Evaluation-only upper bound: this deliberately uses clean ground truth.
    filters["oracle"] = np.abs(target_spec) ** 2 / (np.abs(target_spec) ** 2 + np.abs(noise_spec) ** 2 + 1e-12)
    if masks:
        filters["sam-union"] = image_mask_to_spectrum(np.maximum.reduce(masks), mix_spec.shape)
    if picked:
        filters["muse-sam"] = image_mask_to_spectrum(np.maximum.reduce([masks[i] for i in picked]), mix_spec.shape)
    regions = plan["parsed"].get("regions", [])
    if regions:
        direct = np.zeros((HEIGHT, WIDTH), np.uint8)
        for region in regions[:16]:
            x1, x2 = [int(np.clip(region[k], 0, 1) * WIDTH) for k in ("x1", "x2")]
            y1, y2 = [int(np.clip(region[k], 0, 1) * HEIGHT) for k in ("y1", "y2")]
            if x2 > x1 and y2 > y1:
                direct[y1:y2, x1:x2] = 1
        filters["muse-regions"] = image_mask_to_spectrum(direct, mix_spec.shape)
    outputs, pending = {}, {}
    for method, mask in filters.items():
        # All methods preserve the uncontaminated enrollment interval equally.
        mask[:, np.arange(mask.shape[1]) * HOP / SR < 2] = 1
        audio = istft(mix_spec * mask, len(target))
        path = OUT / f"{name}-{method}.wav"
        write_wav(path, audio)
        cv2.imwrite(str(OUT / f"{name}-{method}-mask.png"), (np.flipud(mask) * 255).astype(np.uint8))
        outputs[method] = {"audio": path.name, "si_sdr_overlap_db": si_sdr(audio[delay:], target[delay:]),
                           "oracle_uses_clean_sources": method == "oracle"}
        pending[method] = pool.submit(transcribe, path)
    outputs["mixture"] = {"audio": mix_path.name, "si_sdr_overlap_db": si_sdr(mixture[delay:], target[delay:]), "asr": raw_future.result()}
    for method, future in pending.items():
        outputs[method]["asr"] = future.result()
    for method, result in outputs.items():
        if "transcript" in result["asr"]:
            result["target_wer"] = wer(reference, result["asr"]["transcript"])
        print(name, method, {k: v for k, v in result.items() if k != "asr"}, result["asr"], flush=True)
    return {"target_to_interference_db": snr_db, "duration_s": duration, "clean_prefix_s": 2,
            "spectrogram_time_extent_s": time_extent, "plan": plan, "sam": records, "selection": selection,
            "selected_mask_ids": picked, "outputs": outputs, "stft_identity_max_error": float(np.max(np.abs(istft(mix_spec, len(target)) - mixture)))}


def main():
    load_env(ROOT / ".env")
    OUT.mkdir(parents=True, exist_ok=True)
    metadata_path = OUT / "target-metadata.json"
    download(ROWS_URL, metadata_path)
    row = json.loads(metadata_path.read_text(encoding="utf-8"))["rows"][0]["row"]
    target_path, background_path = OUT / "target-source.wav", OUT / "background-source.wav"
    target_url = row["audio"][0]["src"]
    with ThreadPoolExecutor(max_workers=3) as pool:
        downloads = [pool.submit(download, target_url, target_path), pool.submit(download, BACKGROUND_URL, background_path)]
        for future in downloads:
            future.result()
        target, background = read_wav(target_path), read_wav(background_path)
        reference = row["text"]
        print("Online target", row["id"], "seconds", len(target) / SR, "reference", reference, flush=True)
        clean_path = OUT / "target-clean.wav"
        write_wav(clean_path, target)
        clean_future = pool.submit(transcribe, clean_path)
        report = {"generated_at": datetime.now(timezone.utc).isoformat(), "provenance": stamp(),
                  "experiment_script_sha256": digest(Path(__file__).read_bytes()),
                  "scope": "One online LibriSpeech target and one online FLEURS interfering speaker, mixed at two levels. TV speech proxy; no room acoustics, actual TV, distress call or population claim.",
                  "target": {"dataset": "https://huggingface.co/datasets/openslr/librispeech_asr", "metadata_url": ROWS_URL,
                             "id": row["id"], "reference": reference, "sha256": digest(target_path.read_bytes())},
                  "interference": {"url": BACKGROUND_URL, "source": "Google FLEURS, en_us test, female speaker, utterance 18066939864018796519; re-encoded by podscripter-project/test-fixtures", "sha256": digest(background_path.read_bytes())},
                  "license": "Both sources CC-BY-4.0. Modifications: repeat/crop interference, RMS scaling, 2-second delay, mixing, spectrogram masking and reconstruction.",
                  "methods": "SAM generic and Muse-directed masks; Muse chooses masks from mixture only. Direct Muse rectangles, ordinary bandpass, raw mixture, and evaluation-only ideal-ratio-mask control. No transcripts or overlapping clean target supplied to separation models or ASR.",
                  "cases": []}
        for snr in (-6, -12):
            report["cases"].append(run_case(target, background, snr, reference, pool))
            save_json(OUT / "result.json", report)
        report["clean_asr"] = clean_future.result()
        if "transcript" in report["clean_asr"]:
            report["clean_target_wer"] = wer(reference, report["clean_asr"]["transcript"])
    save_json(OUT / "result.json", report)
    save_json(ROOT / "evals" / "spectrogram_speech.json", report)
    parts = ["<!doctype html><meta charset='utf-8'><title>Spectrogram speech experiment</title>",
             "<style>body{font:16px system-ui;max-width:1000px;margin:40px auto;padding:20px}audio{width:100%}article{border-top:1px solid #ccc;padding:20px 0}img{max-width:100%}small{color:#555}</style>",
             "<h1>Can image SAM isolate a quieter voice?</h1><p>Controlled overlap of online speech; louder voice is a TV-speech proxy.</p>",
             "<h2>Clean target</h2><audio controls src='target-clean.wav'></audio><p>" + html.escape(reference) + "</p>"]
    for case in report["cases"]:
        parts.append(f"<h2>Interferer {abs(case['target_to_interference_db'])} dB louder</h2>")
        for method, result in case["outputs"].items():
            parts.append(f"<article><h3>{method}</h3><audio controls src='{result['audio']}'></audio><p>" + html.escape(result["asr"].get("transcript", result["asr"].get("error", ""))) + f"</p><small>Target WER: {result.get('target_wer')}; overlap SI-SDR: {result['si_sdr_overlap_db']} dB</small></article>")
    parts.append("<p>Sources: LibriSpeech / OpenSLR (Panayotov et al., ICASSP 2015), Google FLEURS (Conneau et al., 2022); CC-BY 4.0. Mixed, level-adjusted, masked. See result.json for exact URLs, IDs, model requests and limitations.</p>")
    (OUT / "listen.html").write_text("\n".join(parts), encoding="utf-8")
    print("Wrote", OUT / "listen.html", flush=True)


if __name__ == "__main__":
    main()
