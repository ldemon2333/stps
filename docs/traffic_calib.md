# Traffic / Bandwidth Calibration — Phase A Result

Companion to [docs/traffic_optim.md](traffic_optim.md). This note records the
4-card `BW_CAP_4*` calibration sweep and the chosen operating point that
Phase B will use.

## 1. Setup

| Knob | Value |
|---|---|
| cards | 4 |
| tasks | 800 |
| steps | 512 |
| arrival_mode | bursty |
| seeds | 21, 42 |
| fingerprints | synthetic (flat / pulse_t8 / pulse_t16 / bursty), generated on the fly |
| sweep scheduler | rr (reference); bestfit / drf / p2c cross-checked at `BW_CAP*` |

Run:
```
make q-traffic-calib
# or
/root/miniconda3/envs/snn/bin/python script/q_traffic_run.py calib --out-dir data/q_traffic
```

Outputs: `data/q_traffic/calib_raw.csv`, `calib_summary.csv`, `calib_selection.csv`.

## 2. Demand percentiles (uncapped RR, seed=21)

Per-card per-tick demand from steady-state window `load_snapshots[64:-64]`:

| p50 | p75 | p90 | p95 | p99 | max |
|---|---|---|---|---|---|
| 5.40e+05 | 9.00e+05 | 1.34e+06 | 1.52e+06 | 2.08e+06 | 4.74e+06 |

Uncapped baseline: throughput = **1.5195**, p99_delay = 15.0.

## 3. Sweep table (RR mean over 2 seeds)

| bw_cap | throughput | Δthr | cong_ratio | cong_card_frac | mean_wait | p95_wait | timeouts | utilization |
|---|---|---|---|---|---|---|---|---|
| ∞ | 1.5195 | 0.0% | 0.000 | 0.000 | 0.00 | 0 | 0 | 0.000 |
| 5.00e+06 | 1.5195 | 0.0% | 0.000 | 0.000 | 0.00 | 0 | 0 | 0.120 |
| 2.08e+06 (p99) | 1.5195 | 0.0% | 0.001 | 0.009 | 0.19 | 1 | 0 | 0.288 |
| 2.00e+06 | 1.5195 | 0.0% | 0.002 | 0.011 | 0.24 | 1 | 0 | 0.299 |
| 1.52e+06 (p95) | 1.5195 | 0.0% | 0.009 | 0.054 | 1.03 | 4.5 | 0 | 0.393 |
| 1.34e+06 (p90) | 1.5109 | 0.6% | 0.019 | 0.103 | 1.90 | 8.0 | 0 | 0.445 |
| 1.00e+06 | 1.4653 | 3.6% | 0.082 | 0.293 | 5.71 | 17.5 | 0 | 0.589 |
| **9.00e+05 (p75)** ⭐ | **1.4415** | **5.1%** | **0.115** | **0.357** | **6.83** | **19.5** | **0** | **0.649** |
| 5.40e+05 (p50) | 1.1212 | 26.2% | 0.354 | 0.710 | 18.9 | 40 | 0 | 0.855 |
| 5.00e+05 | 1.0638 | 30.0% | 0.383 | 0.728 | 20.6 | 44 | 0 | 0.872 |
| 2.00e+05 | 0.4570 | 69.9% | 0.625 | 0.884 | 59.8 | 123.5 | 0 | 0.928 |
| 1.00e+05 | 0.2407 | 84.2% | 0.769 | 0.939 | 122.0 | 250.5 | 0 | 0.970 |

## 4. Selection

Target band (per [traffic_optim.md](traffic_optim.md) §2.3.2):
- throughput drop ≥10% (primary) / ≥5% (fallback)
- avg_congestion_ratio ∈ [0.1, 0.4]
- congested_card_tick_frac ∈ [0.2, 0.6]
- congestion_timeouts = 0

**No candidate satisfies the strict ≥10% band** under bursty arrival — the curve
jumps from 3.6% (cap=1e6) to 26.2% (cap=p50=5.4e5), reflecting the
bimodal nature of the bursty workload (most ticks are well under p50,
spikes overshoot p95).

Falling back to ≥5% drop + ratio ≥ 0.05, the highest-cap candidate that
matches is **bw_cap = 9.00e+05 (p75)** with:
- 5.1% throughput drop
- avg_congestion_ratio = 0.115 ✓
- congested_card_tick_frac = 0.357 ✓
- congestion_timeouts = 0 ✓
- mean_congestion_wait = 6.83 ticks, p95 = 19.5 ticks ✓

This sits in the **knee** of the throughput-vs-cap curve: tight enough to
expose contention on every burst, loose enough to leave headroom for
schedulers that smooth demand across cards or ticks.

```
BW_CAP_4* = 900_000   (= demand p75 of uncapped 4-card RR run, bursty)
```

## 5. Baseline cross-check at `BW_CAP_4*`

| scheduler | throughput | cong_ratio | cong_card_frac | mean_wait | p95_wait |
|---|---|---|---|---|---|
| rr | 1.4415 | 0.115 | 0.357 | 6.83 | 19.5 |
| bestfit | 1.4441 | 0.098 | 0.323 | 5.96 | 19.5 |
| drf | 1.4441 | 0.098 | 0.323 | 5.96 | 19.5 |
| p2c | 1.4402 | 0.111 | 0.363 | 6.93 | 20.0 |

All four static schedulers collapse onto **≈ 1.44 throughput** (a 5% drop
from uncapped 1.52). The original Q0 observation that "static baselines
are indistinguishable" *still holds under contention*, but now they
share a measurable cost: ~6 ticks of congestion wait per task and 35%
of card-ticks congested. This is the regime where Phase B will test
whether STPS's phase-shift can pay back its forecast cost by lowering
that congestion-wait floor.

## 6. Artifacts

- `data/q_traffic/calib_raw.csv` — every (scheduler, bw_cap, seed) row
- `data/q_traffic/calib_summary.csv` — 2-seed mean ± ci95 aggregation
- `data/q_traffic/calib_selection.csv` — chosen `BW_CAP_4*` + demand percentiles

## 7. Phase B handoff

Phase B (`q-traffic-main`, `q-traffic-sens`) should pin
`--bw-cap 900000` and re-run the STPS family vs baselines on the same
synthetic suite. The sensitivity sweep should probe at least
`[1.34e6, 9.0e5, 5.4e5]` — i.e. the p90 / p75 / p50 anchors — so the
paper can argue robustness across light / target / heavy contention
regimes.
