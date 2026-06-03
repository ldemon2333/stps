#!/usr/bin/env bash
# C-tier (BW_MAX=9e5, tasks=400) D_MAX sweep: D in {2,4,8}. D=16 already on disk.
set -euo pipefail
cd "$(dirname "$0")/.."
PY=/root/miniconda3/envs/snn/bin/python
for D in 2 4 8; do
  echo "=== D_MAX=${D} START $(date +%H:%M:%S) ==="
  "${PY}" script/q2_run.py main4 --bw-max 9e5 --tasks 400 --d-max "${D}" \
      --name "main4_bw9e5_d${D}_t400" --out-dir data/q2
  echo "=== D_MAX=${D} DONE $(date +%H:%M:%S) ==="
done
echo ALL_DONE
