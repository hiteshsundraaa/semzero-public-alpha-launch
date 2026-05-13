#!/usr/bin/env bash
set -euo pipefail
EXTRAS=${1:-}
PYTHON_BIN=${PYTHON_BIN:-python3}
VENV_DIR=${VENV_DIR:-.venv}
$PYTHON_BIN -m venv "$VENV_DIR"
source "$VENV_DIR/bin/activate"
pip install -U pip
if [[ -n "$EXTRAS" ]]; then
  pip install -e ".[$EXTRAS]"
else
  pip install -e .
fi
printf '
SemZero installed. Activate with:
  source %s/bin/activate
Then run:
  semzero commands
  semzero shadow --help
' "$VENV_DIR"
