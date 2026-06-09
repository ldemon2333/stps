#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "$0")/.."
PY=/root/stps/.venv/bin/python
W=${Q1_WORKERS:-28}
echo "=== q1 sweep START $(date +%H:%M:%S) ==="
$PY script/q1_run.py sweep --out-dir data/q1 --workers $W
echo "=== q1 mix START $(date +%H:%M:%S) ==="
$PY script/q1_run.py mix --out-dir data/q1 --workers $W
echo "=== ALL_Q1_DONE $(date +%H:%M:%S) ==="
