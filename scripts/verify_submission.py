"""Run the entire backend suite in an installed venv and save its actual result.

Usage: python scripts/verify_submission.py --out evals/verification.json
The report comes from pytest's JUnit output, never a hard-coded test count.
"""
from __future__ import annotations

import argparse
import importlib.util
import importlib.metadata
import json
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from evidence_stamp import ROOT, stamp


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "evals/verification.json")
    args = parser.parse_args()
    if sys.prefix == sys.base_prefix:
        raise SystemExit("Activate a fresh virtual environment installed from requirements.txt first.")
    origins = {}
    for name in ("fastapi", "starlette", "httpx", "pytest", "cv2", "ultralytics", "meta_sam_parser"):
        spec = importlib.util.find_spec(name)
        if not spec or not spec.origin or not Path(spec.origin).resolve().is_relative_to(Path(sys.prefix).resolve()):
            raise SystemExit(f"{name} is missing or comes from outside this virtual environment.")
        origins[name] = Path(spec.origin).resolve().relative_to(Path(sys.prefix).resolve()).as_posix()
    report = {"provenance": stamp(), "dependency_origins": origins,
              "command": "python -m pytest -q --junitxml=<temporary report>"}
    pins = {}
    for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith(("#", "--")):
            continue
        requirement = Requirement(line)
        if requirement.marker is None or requirement.marker.evaluate():
            pins[canonicalize_name(requirement.name)] = requirement.specifier
    unpinned = []
    for distribution in importlib.metadata.distributions():
        name = canonicalize_name(distribution.metadata["Name"])
        if name == "pip":  # environment bootstrap, not an application dependency
            continue
        specifier = pins.get(name)
        if specifier is None or not str(specifier).startswith("==") or distribution.version not in specifier:
            unpinned.append(f"{name}=={distribution.version}")
    report["dependency_pins"] = {"ok": not unpinned, "unmatched_installed_packages": unpinned}
    check = subprocess.run([sys.executable, "-m", "pip", "check"], cwd=ROOT, capture_output=True, text=True)
    report["pip_check"] = {"exit_code": check.returncode, "output": check.stdout.strip() or check.stderr.strip()}
    work = ROOT / ".tmp"
    work.mkdir(exist_ok=True)
    env = {**os.environ, "TMPDIR": str(work), "TEMP": str(work), "TMP": str(work),
           "YOLO_CONFIG_DIR": str(ROOT / ".ultralytics"), "YOLO_AUTOINSTALL": "false",
           "PYTHONNOUSERSITE": "1"}
    with tempfile.TemporaryDirectory(prefix="submission-", dir=work) as temporary:
        junit = Path(temporary) / "pytest.xml"
        result = subprocess.run([sys.executable, "-m", "pytest", "-q", f"--junitxml={junit}"],
                                cwd=ROOT, env=env, capture_output=True, text=True)
        print(result.stdout)
        if result.stderr:
            print(result.stderr, file=sys.stderr)
        report["pytest_exit_code"] = result.returncode
        if junit.exists():
            suites = ET.parse(junit).getroot().findall("testsuite")
            counts = {key: sum(int(suite.get(key, 0)) for suite in suites)
                      for key in ("tests", "failures", "errors", "skipped")}
            counts["passed"] = counts["tests"] - counts["failures"] - counts["errors"] - counts["skipped"]
            report["results"] = counts
            report["failures"] = [{"test": case.get("name"), "kind": problem.tag,
                                   "message": problem.get("message", "")}
                                  for case in ET.parse(junit).iter("testcase")
                                  for problem in case if problem.tag in ("failure", "error")]
    report["ok"] = not unpinned and check.returncode == 0 and result.returncode == 0
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
