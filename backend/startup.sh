#!/bin/bash
set -euo pipefail
cd /home/site/wwwroot

PACKAGES="/home/site/wwwroot/.python_packages/lib/site-packages"
export PYTHONPATH="${PACKAGES}:${PYTHONPATH:-}"

if [ ! -d "$PACKAGES/uvicorn" ]; then
  echo "WARNING: .python_packages missing; installing (slow). Prefer GitHub deploy with bundled deps."
  mkdir -p "$PACKAGES"
  python -m pip install --upgrade pip
  python -m pip install -r requirements-prod.txt -t "$PACKAGES"
fi

echo "Starting uvicorn on port ${WEBSITES_PORT:-8000}..."
exec python -m uvicorn server:app --host 0.0.0.0 --port "${WEBSITES_PORT:-8000}"
