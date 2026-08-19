#!/usr/bin/env bash
# kinesis launcher: ensure venv + deps + model, then run.
#   ./run.sh          launch the app          (available from M1)
#   ./run.sh m0       M0 pinch check          (add --preview for a camera window)
#   ./run.sh test     pytest
#   ./run.sh setup    just do the setup and stop
set -euo pipefail
cd "$(dirname "$0")"

PY=.venv/bin/python
STAMP=.venv/.deps-installed

if [ ! -x "$PY" ]; then
  if ! command -v python3.12 >/dev/null 2>&1; then
    echo "kinesis needs Python 3.12 (mediapipe wheels do not cover 3.13)." >&2
    echo "Install it:  brew install python@3.12" >&2
    exit 1
  fi
  echo "[kinesis] creating .venv (python3.12)…"
  python3.12 -m venv .venv
  "$PY" -m pip install --quiet --upgrade pip
fi

# Reinstall deps only when pyproject changes.
if [ ! -f "$STAMP" ] || [ pyproject.toml -nt "$STAMP" ]; then
  echo "[kinesis] installing dependencies…"
  "$PY" -m pip install --quiet -e ".[dev]"
  touch "$STAMP"
fi

# Model download is a no-op once cached.
"$PY" -m kinesis.tracking.model || {
  echo "[kinesis] model setup failed — rerun: $PY -m kinesis.tracking.model" >&2
  exit 1
}

cmd="${1:-app}"
[ $# -gt 0 ] && shift || true

case "$cmd" in
  m0)    exec "$PY" scripts/m0_pinch_check.py "$@" ;;
  test)  exec "$PY" -m pytest "$@" ;;
  setup) echo "[kinesis] setup complete." ;;
  app)
    if [ -f kinesis/__main__.py ]; then
      exec "$PY" -m kinesis "$@"
    fi
    echo "[kinesis] The app does not exist yet — it lands in M1."
    echo "          For the M0 checkpoint run:  ./run.sh m0 --preview"
    exit 1
    ;;
  *) echo "usage: ./run.sh [app|m0|test|setup]" >&2; exit 2 ;;
esac
