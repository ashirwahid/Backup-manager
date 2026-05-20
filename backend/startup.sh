#!/bin/bash
set -euo pipefail
cd /home/site/wwwroot

# Stale Oryx artifacts on persistent /home can override a clean GitHub deploy.
rm -f /home/site/wwwroot/output.tar.zst /home/site/wwwroot/oryx-manifest.toml 2>/dev/null || true

PACKAGES="/home/site/wwwroot/.python_packages/lib/site-packages"
export PYTHONNOUSERSITE=1
unset VIRTUAL_ENV

deps_ok() {
  export PYTHONPATH="${PACKAGES}"
  python -c "
import importlib
importlib.import_module('httpcore._backends.sync')
importlib.import_module('fastapi.applications')
import httpx
print('deps ok: httpx', httpx.__version__)
"
}

install_deps() {
  echo "Installing fresh prod dependencies into ${PACKAGES}..."
  rm -rf /home/site/wwwroot/.python_packages
  mkdir -p "${PACKAGES}"
  python -m pip install --upgrade pip
  python -m pip install -r requirements-prod.txt -t "${PACKAGES}" --no-cache-dir
}

export PYTHONPATH="${PACKAGES}"

if ! deps_ok 2>/dev/null; then
  echo "WARNING: .python_packages missing or corrupt (common after mixed Oryx/deploy installs)."
  install_deps
  export PYTHONPATH="${PACKAGES}"
  deps_ok
fi

echo "PYTHONPATH=${PYTHONPATH}"
echo "Starting uvicorn on port ${WEBSITES_PORT:-8000}..."
exec python -m uvicorn server:app --host 0.0.0.0 --port "${WEBSITES_PORT:-8000}"
