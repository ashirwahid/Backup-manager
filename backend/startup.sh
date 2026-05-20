#!/bin/bash
set -euo pipefail
cd /home/site/wwwroot

PACKAGES="/home/site/wwwroot/.python_packages/lib/site-packages"
export PYTHONNOUSERSITE=1
export PYTHONPATH="${PACKAGES}"
unset VIRTUAL_ENV

# Only install on boot when the CI bundle is missing (never block on httpcore verify).
if [ ! -d "${PACKAGES}/uvicorn" ]; then
  echo "WARNING: .python_packages missing; installing prod deps (slow first boot)."
  mkdir -p "${PACKAGES}"
  python -m pip install --upgrade pip
  python -m pip install -r requirements-prod.txt -t "${PACKAGES}" --no-cache-dir
fi

python -c "import importlib; importlib.import_module('httpcore._backends.sync'); import httpx; print('http stack ok:', httpx.__version__)" \
  || echo "WARN: httpcore check failed — redeploy backend with GitHub workflow bundle."

echo "PYTHONPATH=${PYTHONPATH}"
echo "Starting uvicorn on port ${WEBSITES_PORT:-8000}..."
exec python -m uvicorn server:app --host 0.0.0.0 --port "${WEBSITES_PORT:-8000}"
