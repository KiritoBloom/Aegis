"""Start the backend with the repo's env and vendor path.

`set -a; source .env` is bash; on Windows the env file has to be read explicitly
or the API key silently arrives empty. This is the one command that starts the
service, so the two platforms cannot drift apart.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def main() -> None:
    load_env(ROOT / ".env")
    for sub in (".vendor", "backend"):
        p = str(ROOT / sub)
        if p not in sys.path:
            sys.path.insert(0, p)
    for var, sub in (("TMPDIR", ".tmp"), ("TEMP", ".tmp"), ("MEDIA_ROOT", "data/media")):
        os.environ.setdefault(var, str(ROOT / sub))

    import uvicorn
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run("app.api:app", host="127.0.0.1", port=port,
                log_level=os.environ.get("LOG_LEVEL", "info"))


if __name__ == "__main__":
    main()
