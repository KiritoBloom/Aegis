"""Read-only listing of clips used by the lab feed picker."""

from __future__ import annotations

import mimetypes
import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

ROOT = Path(__file__).resolve().parents[2]
FOOTAGE_ROOT = Path(os.environ.get("FOOTAGE_ROOT", str(ROOT / "data" / "new-footage")))
FOOTAGE_SUFFIXES = {".mp4", ".webm", ".mov", ".m4v"}

router = APIRouter()


def footage_path(name: str) -> Path | None:
    clean = Path(name)
    if clean.name != name or clean.suffix.lower() not in FOOTAGE_SUFFIXES:
        return None
    path = FOOTAGE_ROOT / clean.name
    return path if path.is_file() else None


@router.get("/api/demo/videos")
def demo_videos():
    if not FOOTAGE_ROOT.is_dir():
        return {"items": [], "root": str(FOOTAGE_ROOT)}
    items = []
    for path in sorted(FOOTAGE_ROOT.iterdir(), key=lambda p: p.name.lower()):
        if not path.is_file() or path.suffix.lower() not in FOOTAGE_SUFFIXES:
            continue
        stat = path.stat()
        items.append({
            "name": path.name,
            "bytes": stat.st_size,
            "modified": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
            "url": f"/api/demo/videos/{path.name}",
        })
    return {"items": items, "root": str(FOOTAGE_ROOT)}


@router.get("/api/demo/videos/{name}")
def demo_video(name: str):
    path = footage_path(name)
    if path is None:
        raise HTTPException(404, "not found")
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type,
                        headers={"Cache-Control": "private, max-age=300"})
