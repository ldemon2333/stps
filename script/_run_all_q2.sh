#!/usr/bin/env bash
# Regenerate all Q2 datasets backing tab:q2_phase / tab:q2_dmax / tab:q2_regime_*.
# Each invocation = 100 jobs (5 algo x 2 phase x 2 arrival x 5 seed), parallel + resume.
set -uo pipefail
cd "$(dirname "$0")/.."
PY=/root/stps/.venv/bin/python
W=${Q2_WORKERS:-28}
OUT=data/q2
mkdir -p "$OUT"
log() { echo "=== $* $(date +%H:%M:%S) ==="; }

# 1. phase ablation (regime A's d=2 sibling): bw9e5, tasks=800, d2
log "q2 phase main4_bw9e5_d2 START"
$PY script/q2_run.py main4 --bw-max 9e5 --d-max 2  --name main4_bw9e5_d2      --out-dir $OUT --workers $W

# 2-5. dmax sweep, light regime C: bw9e5, tasks=400, d in {2,4,8,16}
for D in 2 4 8 16; do
  log "q2 dmax main4_bw9e5_d${D}_t400 START"
  $PY script/q2_run.py main4 --bw-max 9e5 --tasks 400 --d-max "$D" --name "main4_bw9e5_d${D}_t400" --out-dir $OUT --workers $W
done

# 6. regime A: bw9e5, tasks=800, d16
log "q2 regimeA main4_bw9e5_d16 START"
$PY script/q2_run.py main4 --bw-max 9e5 --d-max 16 --name main4_bw9e5_d16 --out-dir $OUT --workers $W

# 7. regime B: bw5e6, tasks=800, d16
log "q2 regimeB main4_bw5e6_d16 START"
$PY script/q2_run.py main4 --bw-max 5e6 --d-max 16 --name main4_bw5e6_d16 --out-dir $OUT --workers $W

log "ALL_Q2_DONE"
