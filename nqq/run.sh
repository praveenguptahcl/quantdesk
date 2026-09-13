#!/usr/bin/env bash
# nqq launcher — zero dependencies, works out of the box.
set -e
cd "$(dirname "$0")/api"
PY=$(command -v python3 || command -v python)
PORT="${NQQ_PORT:-8900}"
HOST="${NQQ_HOST:-127.0.0.1}"
# open the browser shortly after boot (best-effort; ignore if headless)
( sleep 1.5
  URL="http://${HOST}:${PORT}"
  (command -v open  >/dev/null && open "$URL") \
  || (command -v xdg-open >/dev/null && xdg-open "$URL") \
  || true ) >/dev/null 2>&1 &
exec "$PY" server.py
