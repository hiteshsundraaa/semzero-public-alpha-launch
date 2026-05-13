#!/usr/bin/env bash
set -euo pipefail
python -m pip install -U build
python -m build
printf '
Built artifacts in dist/
'
