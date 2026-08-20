#!/usr/bin/env bash
# kinesis launcher: ensure venv + deps + model, then run.
#   ./run.sh          launch the app          (available from M1)
#   ./run.sh m0       M0 pinch check          (add --preview for a camera window)
#   ./run.sh test     pytest
#   ./run.sh check    every gate: lint, import sweep, tests (pre-push)
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
  check)
    # Every gate the project claims to pass, in one command -- because "I ran the
    # tests" and "I ran the tests AND checked imports" are a keystroke apart and
    # feel identical afterwards. Each gate announces itself, all of them run even
    # after one fails (a lint error should not hide a broken test), and the exit
    # code is non-zero if any did. Adding a gate is one gate line.
    gate_failed=0
    gate() {
      name="$1"; shift
      echo
      echo "[check] --- $name"
      if "$@"; then
        echo "[check] ok: $name"
      else
        echo "[check] FAILED: $name" >&2
        gate_failed=1
      fi
    }

    gate "ruff -- lint and import order" "$PY" -m ruff check .
    # Conventions linter (file size, module docstrings) lands in #17:
    # gate "conventions" "$PY" scripts/conventions_lint.py
    gate "import sweep -- every module in the package imports" "$PY" scripts/import_sweep.py
    gate "pytest -- the full suite" "$PY" -m pytest

    echo
    if [ "$gate_failed" -ne 0 ]; then
      echo "[check] one or more gates failed." >&2
      exit 1
    fi
    echo "[check] all gates green."
    ;;
  setup) echo "[kinesis] setup complete." ;;
  app)
    if [ -f kinesis/__main__.py ]; then
      exec "$PY" -m kinesis "$@"
    fi
    echo "[kinesis] The app does not exist yet — it lands in M1."
    echo "          For the M0 checkpoint run:  ./run.sh m0 --preview"
    exit 1
    ;;
  *) echo "usage: ./run.sh [app|m0|test|check|setup]" >&2; exit 2 ;;
esac
