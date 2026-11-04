"""Provenance shared by measured reports; never label a dirty tree as HEAD."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE_GLOBS = ("backend/**/*.py", "edge/**/*.py", "scripts/*.py", "tests/*.py",
                "requirements*.txt", "constraints*.txt", "pytest.ini", "evals/fixtures/*.json")


def file_hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def stamp() -> dict:
    sources = {path.relative_to(ROOT).as_posix(): file_hash(path)
               for pattern in SOURCE_GLOBS for path in sorted(ROOT.glob(pattern))}
    source_json = json.dumps(sources, sort_keys=True, separators=(",", ":"))
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                            capture_output=True, text=True, check=True).stdout.strip()
    changes = subprocess.run(["git", "status", "--porcelain", "--", *sources], cwd=ROOT,
                             capture_output=True, text=True, check=True).stdout.strip()
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_commit": commit,
        "source_tree_dirty": bool(changes),
        "source_sha256": hashlib.sha256(source_json.encode()).hexdigest(),
        "source_files": sources,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "virtual_environment": sys.prefix != sys.base_prefix,
        "packages": dict(sorted((dist.metadata["Name"], dist.version)
                                for dist in importlib.metadata.distributions())),
    }
