"""Q2 §9 sweep: (BW_MAX, D_MAX) 2x2 grid on 4 cards.

Adds per-card CV reporting + bandwidth/contention sensitivity + phase-shift
delay budget sensitivity. Reuses the 5 algo pairs from q2_run.py.

Grid:
  BW_MAX in {5e6 (uncontended), 9e5 (p75 from traffic_calib.md)}
  D_MAX  in {16, 2}

phase=off ignores D_MAX, so the (D_MAX=16, phase=off) and (D_MAX=2, phase=off)
rows are physical duplicates — they're kept in the raw CSV but the doc only
references one copy per (BW_MAX, algo, arrival).
"""
from __future__ import annotations

import argparse
import csv
import logging
import math
import os
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from statistics import mean, stdev
from typing import Dict, List, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fingerprint import load_fingerprint, save_fingerprint
from simulation.engine import run_simulation

_root = logging.getLogger()
_root.setLevel(logging.ERROR)
if not _root.handlers:
    _root.addHandler(logging.NullHandler())
for name in ("simulation", "schedule", "util", "fingerprint", "simulation.engine"):
    logging.getLogger(name).setLevel(logging.ERROR)


PAIRS = [
    ("rr", "rr-phase", "RR"),
    ("bestfit", "bestfit-phase", "BestFit"),
    ("drf", "drf-phase", "DRF"),
    ("p2c", "p2c-phase", "P2C"),
    ("stps-spatial", "stps", "STPS-spatial"),
]
SEEDS = [21, 42, 99, 123, 2024]
ARRIVALS = ["poisson", "bursty"]
STEPS = 512
TASKS = 800
CARDS = 4
HORIZON = 64
SPLIT_THRESHOLD = 0.2

# 2x2 sweep grid. Both BW values exercise NoC contention
# (traffic_calib.md: BW_CAP_4* = 9e5 = demand p75, 5e5 < p50).
BW_LIST = [9.0e5, 5.0e5]
DMAX_LIST = [16, 2]

MIXED_FINGERPRINTS = [
    "synthetic_flat.npz",
    "synthetic_pulse_t8.npz",
    "synthetic_pulse_t16.npz",
    "synthetic_bursty.npz",
    "spikformer_cifar10.npz",
    "qkformer_cifar10.npz",
    "spikingresformer_ti_imagenet.npz",
]

AGG_METRIC_KEYS = [
    "card_cv", "card_jfi", "card_lif", "max_min_ratio",
    "completion_rate", "throughput", "p99_delay",
    "mean_start_offset", "p95_start_offset", "reject_rate_bw",
    "avg_congestion_ratio", "mean_congestion_wait_ticks",
    "p95_congestion_wait_ticks",
    "time_card_cv_demand_mean", "time_card_lif_demand_mean",
    "time_card_lif_served_mean",
]
PERCARD_METRIC_KEYS = [
    "mean_load", "max_load", "std_load",
    "time_cv", "time_lif", "time_jfi",
    "mean_cong_ratio", "max_cong_ratio",
    "completed_tasks", "throughput",
]


def _ci95(vals: Sequence[float]) -> float:
    if len(vals) < 2:
        return 0.0
    return 1.96 * stdev(vals) / math.sqrt(len(vals))


def _silence_logging() -> None:
    r = logging.getLogger()
    r.setLevel(logging.ERROR)
    if not r.handlers:
        r.addHandler(logging.NullHandler())
    for n in ("simulation", "schedule", "util", "fingerprint", "simulation.engine"):
        logging.getLogger(n).setLevel(logging.ERROR)


def _write_csv(path: Path, rows: List[Dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        return
    fieldnames: List[str] = []
    seen = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                fieldnames.append(key)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _build_mixed_fp_dir(parent: Path, source_dir: Path = ROOT / "npz") -> str:
    parent.mkdir(parents=True, exist_ok=True)
    for name in MIXED_FINGERPRINTS:
        src = source_dir / name
        if not src.exists():
            raise FileNotFoundError(f"Q2 mixed fingerprint missing: {src}")
        save_fingerprint(parent / name, load_fingerprint(src))
    return str(parent)


def _run_one(scheduler, seed, arrival, fp_dir, base_algo, phase_enabled,
             pair_base, pair_phase, bw_max, d_max):
    _silence_logging()
    job_tag = f"{scheduler}_{arrival}_{seed}_bw{int(bw_max)}_d{d_max}"
    metrics = run_simulation(
        scheduler=scheduler,
        cards=CARDS,
        tasks=TASKS,
        steps=STEPS,
        seed=seed,
        arrival_mode=arrival,
        fingerprint_dir=fp_dir,
        bw_max=bw_max,
        bw_cap=bw_max,
        d_max=d_max,
        horizon=HORIZON,
        centrality_split_threshold=SPLIT_THRESHOLD,
        log_dir="log",
        data_dir=f"data/q2/_sweep_raw/{job_tag}",
    )
    agg_row = {
        "scheduler": scheduler,
        "seed": seed,
        "arrival_mode": arrival,
        "base_algo": base_algo,
        "phase_enabled": phase_enabled,
        "pair_base_scheduler": pair_base,
        "pair_phase_scheduler": pair_phase,
        "bw_max": bw_max,
        "d_max": d_max,
        "cards": CARDS,
        "tasks": TASKS,
        "card_cv": metrics.avg_card_cv,
        "card_jfi": metrics.avg_card_jfi,
        "card_lif": metrics.avg_card_lif,
        "max_min_ratio": metrics.avg_max_min_ratio,
        "completion_rate": metrics.completion_rate,
        "throughput": metrics.throughput,
        "p99_delay": metrics.p99_delay,
        "mean_start_offset": metrics.mean_start_offset,
        "p95_start_offset": metrics.p95_start_offset,
        "reject_rate_bw": metrics.reject_rate_bw,
        "avg_congestion_ratio": metrics.avg_congestion_ratio,
        "mean_congestion_wait_ticks": metrics.mean_congestion_wait_ticks,
        "p95_congestion_wait_ticks": metrics.p95_congestion_wait_ticks,
        "time_card_cv_demand_mean": metrics.time_card_cv_demand_mean,
        "time_card_lif_demand_mean": metrics.time_card_lif_demand_mean,
        "time_card_lif_served_mean": metrics.time_card_lif_served_mean,
    }
    pc_rows: List[Dict] = []
    for kind in ("served", "demand"):
        for entry in metrics.per_card_breakdown(kind=kind):
            pc_rows.append({
                "scheduler": scheduler,
                "seed": seed,
                "arrival_mode": arrival,
                "base_algo": base_algo,
                "phase_enabled": phase_enabled,
                "bw_max": bw_max,
                "d_max": d_max,
                **entry,
            })
    return agg_row, pc_rows


def _aggregate(rows, group_keys, metric_keys):
    buckets: Dict[tuple, List[Dict]] = {}
    for row in rows:
        key = tuple(row[k] for k in group_keys)
        buckets.setdefault(key, []).append(row)
    out = []
    for key, items in buckets.items():
        agg = {k: v for k, v in zip(group_keys, key)}
        agg["n"] = len(items)
        for metric in metric_keys:
            vals = [float(r[metric]) for r in items]
            agg[f"{metric}_mean"] = mean(vals)
            agg[f"{metric}_ci95"] = _ci95(vals)
        out.append(agg)
    return out


def _default_workers(n_jobs, mem_per_worker_gb=3.0):
    cpu = os.cpu_count() or 1
    try:
        with open("/proc/meminfo") as f:
            avail_gb = 8.0
            for line in f:
                if line.startswith("MemAvailable:"):
                    avail_gb = int(line.split()[1]) / (1024 * 1024)
                    break
    except OSError:
        avail_gb = 8.0
    mem_cap = max(1, int(avail_gb / mem_per_worker_gb))
    return max(1, min(cpu, mem_cap, n_jobs))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default="data/q2")
    ap.add_argument("--workers", type=int, default=0)
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    with tempfile.TemporaryDirectory(prefix="q2_bwdmax_fp_") as tmp:
        fp_dir = _build_mixed_fp_dir(Path(tmp) / "mixed")

        jobs = []
        for arrival in ARRIVALS:
            for base, phase, label in PAIRS:
                for scheduler in (base, phase):
                    phase_enabled = (scheduler == phase)
                    for bw_max in BW_LIST:
                        for d_max in DMAX_LIST:
                            for seed in SEEDS:
                                jobs.append((scheduler, seed, arrival, fp_dir,
                                             label, phase_enabled, base, phase,
                                             bw_max, d_max))

        workers = args.workers or _default_workers(len(jobs))
        print(f"[Q2.bwdmax] {len(jobs)} jobs, {workers} workers", flush=True)

        agg_rows: List[Dict] = []
        pc_rows: List[Dict] = []
        done = 0
        with ProcessPoolExecutor(max_workers=workers) as pool:
            fut_to_job = {pool.submit(_run_one, *j): j for j in jobs}
            for fut in as_completed(fut_to_job):
                j = fut_to_job[fut]
                sched, seed, arrival, _, label, _, _, _, bw_max, d_max = j
                agg_row, per_card = fut.result()
                agg_rows.append(agg_row)
                pc_rows.extend(per_card)
                done += 1
                print(f"  [{done:>3}/{len(jobs)}] {arrival:7s} {label:13s} "
                      f"{sched:14s} bw={bw_max:.0e} d={d_max} seed={seed}",
                      flush=True)

    _write_csv(out_dir / "bwdmax_raw.csv", agg_rows)
    _write_csv(out_dir / "bwdmax_percard_raw.csv", pc_rows)

    agg_sum = _aggregate(
        agg_rows,
        ["arrival_mode", "base_algo", "phase_enabled", "scheduler",
         "bw_max", "d_max"],
        AGG_METRIC_KEYS,
    )
    _write_csv(out_dir / "bwdmax_summary.csv", agg_sum)

    pc_sum = _aggregate(
        pc_rows,
        ["arrival_mode", "base_algo", "phase_enabled", "scheduler",
         "bw_max", "d_max", "card_id", "kind"],
        PERCARD_METRIC_KEYS,
    )
    _write_csv(out_dir / "bwdmax_percard_summary.csv", pc_sum)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
