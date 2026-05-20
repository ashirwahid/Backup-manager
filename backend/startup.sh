#!/bin/bash
set -euo pipefail
cd /home/site/wwwroot

PACKAGES="/home/site/wwwroot/.python_packages/lib/site-packages"

# Use only bundled deps — do not append Oryx antenv paths (causes httpx/httpcore mismatch).
unset VIRTUAL_ENV
export PYTHONNOUSERSITE=1
export PYTHONPATH="${PACKAGES}"

verify_http_stack() {
  python - <<'PY'
import importlib
importlib.import_module("httpcore._backends.sync")
import httpx
import httpcore
print(f"http stack ok: httpx={httpx.__version__} httpcore={httpcore.__version__}")
PY
}

install_packages() {
  echo "Installing production Python dependencies into ${PACKAGES}..."
  mkdir -p "${PACKAGES}"
  python -m pip install --upgrade pip
  python -m pip install --upgrade --force-reinstall \
    -r requirements-prod.txt \
    -t "${PACKAGES}" \
    --no-cache-dir
}

if [ ! -d "${PACKAGES}/uvicorn" ]; then
  echo "WARNING: .python_packages missing uvicorn; installing all prod deps."
  install_packages
elif ! verify_http_stack 2>/dev/null; then
  echo "WARNING: httpx/httpcore stack broken; reinstalling pinned versions."
  install_packages
fi

verify_http_stack

echo "PYTHONPATH=${PYTHONPATH}"
echo "Starting uvicorn on port ${WEBSITES_PORT:-8000}..."
exec python -m uvicorn server:app --host 0.0.0.0 --port "${WEBSITES_PORT:-8000}"
