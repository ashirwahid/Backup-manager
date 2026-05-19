#!/bin/bash
set -euo pipefail
cd /home/site/wwwroot

PACKAGES="/home/site/wwwroot/.python_packages/lib/site-packages"
mkdir -p "$PACKAGES"

# Install deps if missing (e.g. Oryx used an empty antenv).
if [ ! -d "$PACKAGES/uvicorn" ]; then
  echo "Installing Python packages from requirements-prod.txt..."
  python -m pip install --upgrade pip
  python -m pip install -r requirements-prod.txt -t "$PACKAGES"
fi

export PYTHONPATH="${PACKAGES}:${PYTHONPATH:-}"
echo "PYTHONPATH=${PYTHONPATH}"
exec python -m uvicorn server:app --host 0.0.0.0 --port 8000
