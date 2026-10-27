"""Run the enrichment and escalation loops in their own process.

    python scripts/worker.py

The API is restarted constantly — a code change, a config tweak, a crash in a
route handler. The alert path must not share that fate. With the loops in a
separate process, restarting the dashboard cannot drop a fall that arrived
ninety seconds earlier and is still waiting for its clip, and an unacknowledged
emergency still escalates while the API is down.

Start the API with `AEGIS_WORKER=off` so it does not run a second copy of the
same loops:

    AEGIS_WORKER=off python scripts/serve.py
    python scripts/worker.py

This is a launcher rather than `python -m app.worker` because a stray `app`
package elsewhere on sys.path can win the module name and import an unrelated
project. Inserting our paths here, then importing, makes that impossible.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_env(path: Path) -> None:
    """Same loader as scripts/serve.py: a real env var always wins."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())


def main() -> int:
    load_env(ROOT / ".env")
    for p in (ROOT / ".vendor", ROOT / "backend"):
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))
    os.environ.setdefault("TMPDIR", str(ROOT / ".tmp"))
    os.environ.setdefault("TEMP", str(ROOT / ".tmp"))
    os.environ.setdefault("MEDIA_ROOT", str(ROOT / "data" / "media"))

    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "info").upper(),
        format="%(asctime)s %(levelname)-7s %(name)-12s %(message)s",
        datefmt="%H:%M:%S",
    )

    from app import api, db, notify

    log = logging.getLogger("aegis.worker")

    async def run() -> None:
        db.init()
        log.info("worker up — media=%s push=%s",
                 os.environ.get("MEDIA_ROOT"),
                 "on" if notify.VAPID_PUBLIC else "off")
        await asyncio.gather(api.worker_loop(), api.escalation_loop())

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        log.info("worker stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
