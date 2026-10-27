#!/usr/bin/env bash
# Aegis local runner. Everything installs and caches inside this project.
#
#   ./scripts/run.sh api                 start the backend on :8000
#   ./scripts/run.sh edge [clip] [--rt]  run the edge over a clip
#   ./scripts/run.sh probe               fire the API smoke tests
#
set -euo pipefail
cd "$(dirname "$0")/.."

# --- keep every byte on this drive -----------------------------------------
export YOLO_CONFIG_DIR="$PWD/.ultralytics"
export YOLO_AUTOINSTALL=false            # never let ultralytics pip-install into the system Python
export TMPDIR="$PWD/.tmp"
export TEMP="$PWD/.tmp"
export TMP="$PWD/.tmp"
export PIP_CACHE_DIR="$PWD/.pip-cache"
export PYTHONPATH="$PWD/.vendor:$PWD/backend:$PWD/edge"

# --- secrets ----------------------------------------------------------------
if [ -f .env ]; then set -a; . ./.env; set +a; fi
: "${EDGE_TOKEN:=dev-edge-token}"
export EDGE_TOKEN

cmd="${1:-help}"

case "$cmd" in
  api)
    exec python -m uvicorn app.api:app --host 127.0.0.1 --port 8000 "$@"
    ;;
  edge)
    clip="${2:-data/reels/hallway.mp4}"
    mode="${3:-}"
    args=(--config config/edge.yaml --source "file:$clip")
    if [ "$mode" = "--rt" ]; then
      args+=(--no-loop)
    else
      args+=(--fast --no-loop --seconds 120)
    fi
    exec python -m aegis_edge "${args[@]}"
    ;;
  probe)
    exec python scripts/probe_api.py "${2:-models}" "${3:-}"
    ;;
  *)
    cat <<'EOF'
Aegis
  ./scripts/run.sh api                  backend on http://127.0.0.1:8000
  ./scripts/run.sh edge [clip] [--rt]   edge over a clip (default data/reels/hallway.mp4)
  ./scripts/run.sh probe [name]         API smoke tests: models|text|schema|vision|sam|asr

First run downloads yolov8n weights into the repo and takes ~30s.
EOF
    ;;
esac
