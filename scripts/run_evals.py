"""Run every fixture through the real model and write evals/report.md.

This is the artifact that answers "is this a prompt wrapped in a UI?". It runs
the actual Muse Spark call, applies the actual validator, and asserts the
properties the plan promises — so a regression in the prompt, the schema, the
floor, or the evidence gate shows up as a number that moved, not a vibe.

    python scripts/run_evals.py                # 3 runs per fixture (the plan's protocol)
    python scripts/run_evals.py --runs 1       # quick pass while iterating
    python scripts/run_evals.py --only 04,17   # a subset by id prefix

Assertions, per the plan §10.9:
    ORDER[out.severity] >= ORDER[fixture.policy_floor]
    len(out.body) <= 240
    every action id in ALLOWED_ACTIONS
    no forbidden jargon in body
    evidence non-empty
    follow_up_s >= 0
plus the per-fixture `expect` block: exact/min/max severity, actions that must
be offered, words the body must contain, and words it must not.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / ".vendor"))
sys.path.insert(0, str(ROOT / "backend"))

from app import policy  # noqa: E402
from app.meta import MetaClient, MetaError  # noqa: E402
from app.prompt import DECISION_SCHEMA, SYSTEM_PROMPT  # noqa: E402

FIXTURES = ROOT / "evals" / "fixtures"
REPORT = ROOT / "evals" / "report.md"
RESULTS = ROOT / "evals" / "results.json"


def load_env() -> None:
    import os
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


def check(fixture: dict, raw: dict, decision: policy.Decision,
          violations: list[str]) -> list[str]:
    """Every assertion that failed, in plain language a reader can act on."""
    fails: list[str] = []
    exp = fixture.get("expect") or {}
    floor = fixture["policy_floor"]
    sev = decision.severity

    if policy.ORDER[sev] < policy.ORDER[floor]:
        fails.append(f"severity {sev} below floor {floor}")
    if exp.get("exact_severity") and sev != exp["exact_severity"]:
        fails.append(f"expected exactly {exp['exact_severity']}, got {sev}")
    if exp.get("min_severity") and policy.ORDER[sev] < policy.ORDER[exp["min_severity"]]:
        fails.append(f"severity {sev} below expected {exp['min_severity']}")
    if exp.get("max_severity") and policy.ORDER[sev] > policy.ORDER[exp["max_severity"]]:
        # `info` is a policy-floor value, not a model value: the decision schema's
        # severity enum stops at `watch`. So the meaningful restraint assertion
        # for an info ceiling is "quietest expressible severity AND no buzz".
        if not (exp["max_severity"] == "info" and sev == "watch"
                and not decision.notify_now):
            fails.append(f"severity {sev} above expected max {exp['max_severity']}")
    if exp.get("notify_false") and decision.notify_now:
        fails.append("notify_now true on a scenario that must stay quiet")

    body = (decision.body or "").lower()
    if len(decision.body or "") > exp.get("max_body", 240):
        fails.append(f"body {len(decision.body)} chars over limit")
    if exp.get("body_mentions"):
        for word in exp["body_mentions"]:
            if word.lower() not in body:
                fails.append(f"body never mentions {word!r}")
    if exp.get("body_mentions_any"):
        if not any(w.lower() in body for w in exp["body_mentions_any"]):
            fails.append(f"body mentions none of {exp['body_mentions_any']}")
    if exp.get("body_excludes"):
        for word in exp["body_excludes"]:
            if word.lower() in body:
                fails.append(f"body wrongly mentions {word!r}")

    ids = [a.get("id") for a in decision.actions]
    if not all(a in policy.ALLOWED_ACTIONS for a in ids):
        fails.append(f"unknown action id in {ids}")
    for need in exp.get("actions_include", []):
        if need not in ids:
            fails.append(f"missing action {need}")

    for term in policy.FORBIDDEN_TERMS:
        if term in f" {body} ":
            fails.append(f"forbidden jargon {term.strip()!r} in body")

    if not decision.evidence:
        fails.append("evidence empty")
    if decision.follow_up_s < 0:
        fails.append("negative follow_up_s")

    # The gate's own rejects are a failure of this fixture: the model cited
    # something the packet does not contain.
    for v in violations:
        if v.startswith("dropped_evidence"):
            fails.append(v)
    return fails


def run_one(client: MetaClient, fixture: dict) -> dict:
    packet = fixture["packet"]
    t0 = time.time()
    try:
        raw, usage = client.decide(packet, SYSTEM_PROMPT, DECISION_SCHEMA, "low",
                                   max_tokens=4000)
    except (MetaError, ValueError, KeyError) as exc:
        return {"id": fixture["id"], "ok": False, "error": str(exc)[:300],
                "latency_ms": int((time.time() - t0) * 1000)}
    latency = int((time.time() - t0) * 1000)

    decision = policy.Decision(
        severity=raw.get("severity", "info"),
        notify_now=bool(raw.get("notify_now")),
        title=str(raw.get("title", ""))[:120],
        body=str(raw.get("body", ""))[:400],
        evidence=[str(e) for e in (raw.get("evidence") or [])][:8],
        actions=[a for a in (raw.get("actions") or []) if isinstance(a, dict)][:3],
        confidence=float(raw.get("confidence") or 0.5),
        follow_up_s=int(raw.get("follow_up_s") or 0),
        escalate_if_no_ack=bool(raw.get("escalate_if_no_ack")),
        rationale=str(raw.get("rationale", ""))[:300],
    )
    gate = policy.EvidenceGate(packet)
    decision, violations = policy.validate(decision, _floor(fixture), gate)
    fails = check(fixture, raw, decision, violations)

    return {
        "id": fixture["id"], "ok": not fails, "fails": fails,
        "severity": decision.severity, "floor": fixture["policy_floor"],
        "title": decision.title, "body": decision.body,
        "evidence": decision.evidence, "actions": [a.get("id") for a in decision.actions],
        "violations": violations, "latency_ms": latency,
        "tokens_in": usage.input_tokens,
        "tokens_out": usage.output_tokens,
        "cost_usd": usage.cost_usd,
    }


def _floor(fixture: dict) -> policy.Floor:
    return policy.Floor(fixture["policy_floor"], fixture.get("floor_reasons") or [])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--only", default="")
    ap.add_argument("--sleep", type=float, default=0.4)
    args = ap.parse_args()
    load_env()

    import os
    key = os.environ.get("META_API_KEY", "")
    if not key:
        print("META_API_KEY is not set; cannot run the real model.")
        return 2
    client = MetaClient(key)

    only = [s.strip() for s in args.only.split(",") if s.strip()]
    files = sorted(FIXTURES.glob("*.json"))
    if only:
        files = [f for f in files if any(f.stem.startswith(o) for o in only)]
    if not files:
        print("no fixtures found; run scripts/build_fixtures.py first")
        return 2

    print(f"running {len(files)} fixtures x {args.runs} runs against muse-spark-1.3\n")
    results = []
    for path in files:
        fixture = json.loads(path.read_text(encoding="utf-8"))
        runs = []
        for i in range(args.runs):
            r = run_one(client, fixture)
            runs.append(r)
            mark = "ok  " if r["ok"] else "FAIL"
            detail = "" if r["ok"] else f"  <- {r.get('fails', r.get('error'))}"
            print(f"  {mark} {r['id']:<32} run{i + 1} {r.get('severity', '?'):<10}"
                  f"{r.get('latency_ms', 0):>6}ms{detail}")
            time.sleep(args.sleep)
        passed = sum(1 for r in runs if r["ok"])
        results.append({"id": fixture["id"], "why": fixture.get("why", ""),
                        "floor": fixture["policy_floor"], "runs": runs,
                        "pass_rate": passed / len(runs)})

    write_report(results, args.runs)
    total = sum(r["pass_rate"] for r in results)
    full = sum(1 for r in results if r["pass_rate"] == 1.0)
    print(f"\n{full}/{len(results)} fixtures passed every run "
          f"({total / len(results) * 100:.1f}% of fixture-runs clean)")
    print(f"report: {REPORT.relative_to(ROOT)}")
    return 0 if full == len(results) else 1


def write_report(results: list[dict], runs_per: int) -> None:
    flat = [r for f in results for r in f["runs"] if r.get("ok") is not None]
    lat = sorted(r["latency_ms"] for r in flat if r.get("latency_ms"))
    p50 = statistics.median(lat) if lat else 0
    p95 = lat[max(0, int(len(lat) * 0.95) - 1)] if lat else 0
    tin = sum(r.get("tokens_in", 0) for r in flat)
    tout = sum(r.get("tokens_out", 0) for r in flat)
    cost = sum(r.get("cost_usd", 0.0) for r in flat)
    per_decision = cost / len(flat) if flat else 0
    passing = sum(1 for f in results if f["pass_rate"] == 1.0)
    any_pass = sum(1 for f in results if f["pass_rate"] > 0)

    worst = sorted(
        [f for f in results if f["pass_rate"] < 1.0],
        key=lambda f: f["pass_rate"])[:3]

    lines = [
        "# Aegis eval report",
        "",
        f"Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} "
        f"against `muse-spark-1.3`, {runs_per} run(s) per fixture.",
        "",
        "Every figure below is measured by this run. Nothing here is a target.",
        "",
        "## Headline",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Fixtures | {len(results)} |",
        f"| Passing every run | {passing} / {len(results)} "
        f"({passing / len(results) * 100:.0f}%) |",
        f"| Passing at least one run | {any_pass} / {len(results)} |",
        f"| Decisions measured | {len(flat)} |",
        f"| Latency p50 | {p50:.0f} ms |",
        f"| Latency p95 | {p95:.0f} ms |",
        f"| Tokens in / out | {tin:,} / {tout:,} |",
        f"| Cost per decision | ${per_decision:.4f} |",
        "",
        "## What is asserted",
        "",
        "Not vibes. Each decision is checked for: severity at or above the "
        "deterministic policy floor, no severity above a ceiling where the "
        "scenario demands restraint, body length, required and forbidden words, "
        "action ids drawn only from the fixed vocabulary, absence of jargon, "
        "non-empty evidence, and zero invented evidence tokens.",
        "",
        "## Results",
        "",
        "| # | Scenario | Floor | Runs passing | Severity | Latency p50 |",
        "|---|---|---|---|---|---|",
    ]
    for f in results:
        lats = sorted(r["latency_ms"] for r in f["runs"] if r.get("latency_ms"))
        med = statistics.median(lats) if lats else 0
        sevs = ", ".join(sorted({r.get("severity", "?") for r in f["runs"]}))
        mark = "yes" if f["pass_rate"] == 1.0 else f"{f['pass_rate'] * 100:.0f}%"
        lines.append(f"| {f['id'].split('_')[0]} | {f['id'][3:].replace('_', ' ')} | "
                     f"{f['floor']} | {mark} | {sevs} | {med:.0f} ms |")

    if worst:
        lines += ["", "## Worst failures", ""]
        for f in worst:
            bad = next((r for r in f["runs"] if not r["ok"]), None)
            lines.append(f"**{f['id']}** — {f['why']}")
            if bad:
                if bad.get("error"):
                    lines.append(f"- error: `{bad['error']}`")
                for reason in (bad.get("fails") or [])[:4]:
                    lines.append(f"- {reason}")
                if bad.get("body"):
                    lines.append(f"- model wrote: _{bad['body']}_")
            lines.append("")
    else:
        lines += ["", "No fixture failed. ", ""]

    lines += [
        "## How to reproduce",
        "",
        "```bash",
        "python scripts/build_fixtures.py   # 30 packets, floors computed by policy.evaluate",
        "python scripts/run_evals.py        # 3 runs each against the real model",
        "```",
        "",
        "The fixtures are generated, not hand-written, so the expected floor is "
        "produced by the same function the runtime uses. If the policy changes, "
        "the fixtures change with it and this report stops being able to pass by "
        "accident.",
        "",
        "## Limits of this measurement",
        "",
        "- These are packets, not videos. Perception accuracy is measured "
        "separately on real surveillance footage (see `evals/perception.md`).",
        "- 30 fixtures is a regression suite, not a statistically meaningful "
        "sample. It is designed to catch a prompt change that breaks the floor, "
        "the evidence gate, or the tone — not to estimate real-world recall.",
        "- Latency here excludes the SAM pass, which dominates end-to-end time.",
        "",
    ]
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines), encoding="utf-8")
    RESULTS.write_text(json.dumps(results, indent=2), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
