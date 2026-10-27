"""Does the voice check-in change an outcome the way the plan claims?

    python scripts/run_checkin_evals.py

The claim under test is narrow and checkable: a transcript may lower an alert
only when perception independently agrees, and five specific contradiction
shapes must never resolve. This is the row in the plan that says
"Model use: voice changes an outcome | 5 contradiction fixtures, 5/5 escalated".

These are real transcribe calls against generated speech, so the classifier is
tested on what the ASR actually returns rather than on strings I typed. If the
machine has no speech synthesiser the script says so and exits 2 rather than
silently passing on hand-written text.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT / ".vendor", ROOT / "backend"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

REPORT = ROOT / "evals" / "checkin.md"
RESULTS = ROOT / "evals" / "checkin.json"

# Each case is (spoken words, perception, expected resolution, why).
# `perception` is what the camera layer would independently know at that moment.
CASES = [
    {
        "id": "contradiction_floor",
        "say": "I'm fine, I dropped the remote.",
        "perception": {"on_floor": True, "upright_moving": False, "still_s": 14.0},
        "expect": "contradiction",
        "expect_delta": 0,
        "why": "Says she is fine from the floor. The camera disagrees. Must not resolve.",
    },
    {
        "id": "contradiction_still",
        "say": "I am okay, really.",
        "perception": {"on_floor": False, "upright_moving": True, "still_s": 22.0},
        "expect": "contradiction",
        "expect_delta": 0,
        "why": "Motionless for 22s while claiming to be fine is the same contradiction.",
    },
    {
        "id": "negated_affirmative",
        "say": "I'm not okay, I can't get up.",
        "perception": {"on_floor": True, "upright_moving": False, "still_s": 12.0},
        "expect": "escalated",
        "expect_delta": 1,
        "why": "Contains the word 'okay'. Negation must be read first.",
    },
    {
        "id": "help_explicit",
        "say": "Please help me, I need help.",
        "perception": {"on_floor": False, "upright_moving": True, "still_s": 0.0},
        "expect": "escalated",
        "expect_delta": 1,
        "why": "The highest-value word in the system escalates even if she looks calm.",
    },
    {
        "id": "self_recovered",
        "say": "I'm fine, I just tripped. I'm up now.",
        "perception": {"on_floor": False, "upright_moving": True, "still_s": 0.0},
        "expect": "self_recovered",
        "expect_delta": -1,
        "why": "The only path down: she says fine AND she is on her feet and moving.",
    },
]


def load_env() -> None:
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())


def synth(text: str, out: Path) -> bool:
    """Speak `text` to a 16 kHz mono WAV. Windows only, via SAPI."""
    script = (
        "Add-Type -AssemblyName System.Speech;"
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
        "$f = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo("
        "16000,[System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,"
        "[System.Speech.AudioFormat.AudioChannel]::Mono);"
        f"$s.SetOutputToWaveFile('{out}', $f);"
        f"$s.Speak('{text.replace(chr(39), chr(39) + chr(39))}');"
        "$s.Dispose()"
    )
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                           capture_output=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0 and out.exists() and out.stat().st_size > 1024


def main() -> int:
    load_env()
    if not os.environ.get("META_API_KEY"):
        print("META_API_KEY is not set; cannot run the real model.")
        return 2

    from app import policy
    from app.meta import MetaClient, MetaError

    client = MetaClient(os.environ["META_API_KEY"])
    work = Path(tempfile.mkdtemp(prefix="aegis_checkin_"))
    results = []

    for case in CASES:
        wav = work / f"{case['id']}.wav"
        if not synth(case["say"], wav):
            print("no speech synthesiser available; this check-in eval needs real audio")
            return 2
        try:
            transcript = client.transcribe(wav)
        except MetaError as exc:
            print(f"FAIL {case['id']}: transcribe failed: {exc}")
            results.append({**case, "transcript": "", "error": str(exc)[:200], "ok": False})
            continue

        cls = policy.classify_transcript(transcript)
        verdict = policy.checkin_outcome(
            cls,
            upright_moving=case["perception"]["upright_moving"],
            on_floor=case["perception"]["on_floor"],
            still_s=case["perception"]["still_s"],
        )
        ok = (verdict["resolution"] == case["expect"]
              and verdict["severity_delta"] == case["expect_delta"])
        results.append({
            "id": case["id"], "said": case["say"], "transcript": transcript,
            "class": cls, "expect": case["expect"], "got": verdict["resolution"],
            "expect_delta": case["expect_delta"], "delta": verdict["severity_delta"],
            "why": case["why"], "ok": ok,
        })
        print(f"{'ok  ' if ok else 'FAIL'} {case['id']:<22} {cls:<12} "
              f"-> {verdict['resolution']:<16} ({verdict['severity_delta']:+d})")

    passed = sum(1 for r in results if r["ok"])
    write_report(results, passed)
    print(f"\n{passed}/{len(results)} check-in cases behaved correctly")
    print(f"report: {REPORT.relative_to(ROOT)}")
    return 0 if passed == len(results) else 1


def write_report(results: list[dict], passed: int) -> None:
    RESULTS.write_text(json.dumps(results, indent=2), encoding="utf-8")
    lines = [
        "# Aegis check-in eval",
        "",
        "Does the voice check-in change an outcome the way the plan claims?",
        "",
        "Every transcript below came back from `muse-voice-transcribe-1.0` on",
        "synthesised speech, not from a string typed into the test. The",
        "classifier and the de-escalation table then run on that real output.",
        "",
        f"**{passed}/{len(results)} cases behaved correctly.**",
        "",
        "| Case | She said | Transcript | Class | Perception | Outcome | Delta |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in results:
        if "error" in r:
            lines.append(f"| {r['id']} | {r['said']} | _transcribe failed_ | — | — | — | — |")
            continue
        perc = ("on floor" if r.get("perception", {}).get("on_floor")
                else ("still" if r.get("perception", {}).get("still_s", 0) > 10 else "upright"))
        mark = "" if r["ok"] else " **FAIL**"
        lines.append(
            f"| {r['id']}{mark} | {r['said']} | {r['transcript']} | `{r['class']}` | "
            f"{perc} | `{r['got']}` | {r['delta']:+d} |"
        )
    lines += [
        "",
        "## Why each case exists",
        "",
    ]
    for r in results:
        lines.append(f"- **{r['id']}** — {r['why']}")
    lines += [
        "",
        "## The rule under test",
        "",
        "One channel can never lower an alert. Two agreeing channels can.",
        "",
        "- `affirmative` + upright and moving is the **only** path to de-escalation.",
        "- `affirmative` from the floor, or while motionless, is a **contradiction**:",
        "  severity is unchanged and the alert page says she said she is fine but",
        "  has not stood up.",
        "- `help` escalates one tier even if perception looks calm. It is the",
        "  highest-value word in the system.",
        "- `no_response` escalates after 20s. Silence is data.",
        "- `confused` / `other` changes nothing and is marked for the family.",
        "",
        "## Limits",
        "",
        "Five cases, five real ASR calls. This is a regression suite for the",
        "de-escalation table, not a measure of speech recognition accuracy in a",
        "noisy hallway. The audio is clean synthetic speech, which is the easiest",
        "possible input for ASR; a real kitchen at 3 AM is not.",
    ]
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
