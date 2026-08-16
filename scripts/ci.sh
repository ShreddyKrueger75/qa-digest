#!/usr/bin/env bash
set -euo pipefail

if [[ -n "${PYTHON_BIN:-}" ]]; then
  : "use the caller-provided interpreter"
elif [[ -x ".venv/bin/python" ]]; then
  PYTHON_BIN=".venv/bin/python"
else
  PYTHON_BIN="python3"
fi

if ! "$PYTHON_BIN" -c "import pytest, PIL, numpy" >/dev/null 2>&1; then
  echo "[ci] Missing test dependencies for $PYTHON_BIN" >&2
  echo "[ci] Set PYTHON_BIN or run: python3 -m venv --system-site-packages .venv && .venv/bin/python -m pip install pytest Pillow numpy" >&2
  exit 2
fi

echo "[ci] Python: $($PYTHON_BIN --version 2>&1)"
echo "[ci] Compile check"
"$PYTHON_BIN" -m compileall -q scripts watch

echo "[ci] Unit tests"
"$PYTHON_BIN" -m pytest -q tests

echo "[ci] PASS"
