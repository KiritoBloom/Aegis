"""Local rendering for the spoken check-in prompt."""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

log = logging.getLogger("aegis.tts")

_SAPI = r"""
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
if ($env:AEGIS_TTS_VOICE) {
  try { $s.SelectVoice($env:AEGIS_TTS_VOICE) } catch { }
}
$s.Rate = -1
$s.SetOutputToWaveFile($env:AEGIS_TTS_PATH)
$s.Speak($env:AEGIS_TTS_TEXT)
$s.Dispose()
"""


def _powershell() -> str | None:
    for name in ("powershell", "pwsh"):
        found = shutil.which(name)
        if found:
            return found
    return None


def _speak_windows(text: str, path: Path, voice: str | None) -> bool:
    shell = _powershell()
    if not shell:
        return False
    env = dict(os.environ, AEGIS_TTS_PATH=str(path), AEGIS_TTS_TEXT=text)
    if voice:
        env["AEGIS_TTS_VOICE"] = voice
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.run(
            [shell, "-NoProfile", "-NonInteractive", "-Command", _SAPI],
            capture_output=True, text=True, timeout=30, env=env, creationflags=flags,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.warning("tts failed to start: %s", exc)
        return False
    if proc.returncode != 0:
        log.warning("tts exited %s: %s", proc.returncode, (proc.stderr or "")[:200])
        return False
    return path.is_file() and path.stat().st_size > 1000


def _speak_espeak(text: str, path: Path, voice: str | None) -> bool:
    binary = shutil.which("espeak-ng") or shutil.which("espeak")
    if not binary:
        return False
    args = [binary, "-w", str(path)]
    if voice:
        args += ["-v", voice]
    args.append(text)
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.warning("espeak failed: %s", exc)
        return False
    return proc.returncode == 0 and path.is_file() and path.stat().st_size > 1000


def speak_to_wav(text: str, path: str | Path, voice: str | None = None) -> bool:
    """Render `text` to `path`. Never raises; a missing voice is not an incident."""
    text = " ".join(str(text or "").split())
    if not text:
        return False
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.unlink(missing_ok=True)
    voice = voice or os.environ.get("CHECKIN_TTS_VOICE") or None
    ok = _speak_windows(text, target, voice) if sys.platform == "win32" else _speak_espeak(text, target, voice)
    if not ok:
        target.unlink(missing_ok=True)
        log.warning("no local TTS voice available; prompt stays text-only")
    return ok
