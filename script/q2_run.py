"""Q2 phase-shift vertical ablation — 4-card focus, parallel.

For each base scheduler (rr / bestfit / drf / p2c / stps-spatial) we compare it
against its `+phase-shift` counterpart on a mixed-fingerprint workload. Reports
both cluster-aggregate metrics and per-card time-axis metrics so the user can
see how phase-shift reshapes each individual card's load curve.

Parallelism: ProcessPoolExecutor with memory-aware default worker count.
"""
from __future__ import annotations

import argparse
import csv
import logging
import math
import multiprocessing as mp
import os
import sys
import tempfile
from pathlib import Path
from statistics import mean, stdev
from typing import Dict, List, Sequence, Tuple

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
# Defaults; can be overridden per-run via CLI (--bw-max / --d-max).
DEFAULT_BW_MAX = 9.0e5
DEFAULT_D_MAX = 16
HORIZON = 64
SPLIT_THRESHOLD = 0.2
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
    "card_cv", "median_card_cv", "p95_card_cv", "std_card_cv",
    "card_jfi", "card_lif", "max_min_ratio",
    "completion_rate", "throughput", "throughput_excl_cold",
    "mean_cold_start",
    "p99_delay", "p99_delay_excl_cold",
    "avg_delay", "avg_delay_excl_cold",
    "mean_start_offset", "p95_start_offset", "reject_rate_bw",
    "avg_congestion_ratio", "mean_congestion_wait_ticks",
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


def _append_csv(path: Path, rows: List[Dict], fieldnames: Sequence[str]) -> None:
    """Append rows to a CSV file, creating header on first write."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        return
    new_file = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fieldnames))
        if new_file:
            writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fieldnames})


def _read_existing_keys(path: Path,
                        key_cols: Sequence[str]) -> set:
    """Return the set of tuples for ``key_cols`` already in ``path``."""
    if not path.exists() or path.stat().st_size == 0:
        return set()
    keys: set = set()
    with path.open("r", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            keys.add(tuple(row[c] for c in key_cols))
    return keys


def _load_csv_rows(path: Path) -> List[Dict]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open("r", newline="") as handle:
        return list(csv.DictReader(handle))


def _build_mixed_fp_dir(parent: Path, source_dir: Path = ROOT / "npz") -> str:
    parent.mkdir(parents=True, exist_ok=True)
    for name in MIXED_FINGERPRINTS:
        src = source_dir / name
        if not src.exists():
            raise FileNotFoundError(f"Q2 mixed fingerprint missing: {src}")
        save_fingerprint(parent / name, load_fingerprint(src))
    return str(parent)


def _run_one(scheduler: str, seed: int, cards: int, tasks: int,
             arrival: str, fp_dir: str, base_algo: str,
             phase_enabled: bool, pair_base: str, pair_phase: str,
             bw_max: float, d_max: int):
    _silence_logging()
    job_tag = f"{scheduler}_{arrival}_{seed}"
    metrics = run_simulation(
        scheduler=scheduler,
        cards=cards,
        tasks=tasks,
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
        data_dir=f"data/q2/_raw/{job_tag}",
    )
    agg_row = {
        "scheduler": scheduler,
        "seed": seed,
        "cards": cards,
        "tasks": tasks,
        "arrival_mode": arrival,
        "base_algo": base_algo,
        "phase_enabled": phase_enabled,
        "pair_base_scheduler": pair_base,
        "pair_phase_scheduler": pair_phase,
        "bw_max": bw_max,
        "d_max": d_max,
        "card_cv": metrics.avg_card_cv,
        "median_card_cv": metrics.median_card_cv,
        "p95_card_cv": metrics.p95_card_cv,
        "std_card_cv": metrics.std_card_cv,
        "card_jfi": metrics.avg_card_jfi,
        "card_lif": metrics.avg_card_lif,
        "max_min_ratio": metrics.avg_max_min_ratio,
        "completion_rate": metrics.completion_rate,
        "throughput": metrics.throughput,
        "throughput_excl_cold": metrics.throughput_excl_cold,
        "mean_cold_start": metrics.mean_cold_start,
        "p99_delay": metrics.p99_delay,
        "p99_delay_excl_cold": metrics.p99_delay_excl_cold,
        "avg_delay": metrics.avg_delay,
        "avg_delay_excl_cold": metrics.avg_delay_excl_cold,
        "mean_start_offset": metrics.mean_start_offset,
        "p95_start_offset": metrics.p95_start_offset,
        "reject_rate_bw": metrics.reject_rate_bw,
        "avg_congestion_ratio": metrics.avg_congestion_ratio,
        "mean_congestion_wait_ticks": metrics.mean_congestion_wait_ticks,
        "time_card_cv_demand_mean": metrics.time_card_cv_demand_mean,
        "time_card_lif_demand_mean": metrics.time_card_lif_demand_mean,
        "time_card_lif_served_mean": metrics.time_card_lif_served_mean,
    }
    # Per-card rows: report both served and demand views.
    pc_rows: List[Dict] = []
    for kind in ("served", "demand"):
        for entry in metrics.per_card_breakdown(kind=kind):
            pc_rows.append({
                "scheduler": scheduler,
                "seed": seed,
                "cards": cards,
                "arrival_mode": arrival,
                "base_algo": base_algo,
                "phase_enabled": phase_enabled,
                **entry,
            })
    return agg_row, pc_rows


def _aggregate(rows: List[Dict], group_keys: Sequence[str],
               metric_keys: Sequence[str]) -> List[Dict]:
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


def _default_workers(n_jobs: int, mem_per_worker_gb: float = 1.5) -> int:
    """CPU-and-RAM-aware worker count.

    4-card / 800-task / 512-step runs are light (<1 GB/worker), so cap is
    typically CPU-bound.
    """
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


def _run_one_kw(kwargs: Dict) -> Tuple[Dict, List[Dict]]:
    return _run_one(**kwargs)


def run_matrix(out_dir: Path, name: str, cards: int, tasks: int,
               fp_dir: str, workers: int,
               bw_max: float = DEFAULT_BW_MAX,
               d_max: int = DEFAULT_D_MAX,
               resume: bool = True) -> None:
    print(f"[Q2.{name}] cards={cards} tasks={tasks} steps={STEPS} "
          f"bw_max={bw_max:g} d_max={d_max} mixed fingerprints")

    raw_path = out_dir / f"{name}_raw.csv"
    pc_raw_path = out_dir / f"{name}_percard_raw.csv"

    job_keys = ("arrival_mode", "scheduler", "seed")
    existing = _read_existing_keys(raw_path, job_keys) if resume else set()
    if existing:
        print(f"[Q2.{name}] resume: skipping {len(existing)} already-done jobs")

    job_specs: List[Dict] = []
    for arrival in ARRIVALS:
        for base, phase, label in PAIRS:
            for scheduler in (base, phase):
                phase_enabled = (scheduler == phase)
                for seed in SEEDS:
                    key = (arrival, scheduler, str(seed))
                    if key in existing:
                        continue
                    job_specs.append({
                        "scheduler": scheduler,
                        "seed": seed,
                        "cards": cards,
                        "tasks": tasks,
                        "arrival": arrival,
                        "fp_dir": fp_dir,
                        "base_algo": label,
                        "phase_enabled": phase_enabled,
                        "pair_base": base,
                        "pair_phase": phase,
                        "bw_max": bw_max,
                        "d_max": d_max,
                    })

    if not job_specs:
        print(f"[Q2.{name}] all jobs already done; aggregating only")
    else:
        if workers == 0:
            workers = _default_workers(len(job_specs))
        total_jobs = len(existing) + len(job_specs)
        print(f"[Q2.{name}] {len(job_specs)} jobs (of {total_jobs} total), "
              f"{workers} workers", flush=True)

        # Determine field order by running the first job (writes incrementally).
        agg_fields: List[str] = []
        pc_fields: List[str] = []
        done = len(existing)
        ctx = mp.get_context("spawn")
        with ctx.Pool(processes=workers, maxtasksperchild=1) as pool:
            for agg_row, per_card in pool.imap_unordered(
                _run_one_kw, job_specs
            ):
                if not agg_fields:
                    agg_fields = list(agg_row.keys())
                    pc_fields = list(per_card[0].keys()) if per_card else []
                _append_csv(raw_path, [agg_row], agg_fields)
                _append_csv(pc_raw_path, per_card, pc_fields)
                done += 1
                sched = agg_row["scheduler"]
                arrival = agg_row["arrival_mode"]
                seed = agg_row["seed"]
                label = agg_row["base_algo"]
                print(f"  [{done:>3}/{total_jobs}] {arrival:7s} "
                      f"{label:13s} {sched:14s} seed={seed}",
                      flush=True)

    # Aggregate from the on-disk raw CSV so summary is consistent
    # whether or not we resumed.
    all_agg = _load_csv_rows(raw_path)
    all_pc = _load_csv_rows(pc_raw_path)

    agg_sum = _aggregate(
        all_agg,
        ["cards", "arrival_mode", "base_algo", "phase_enabled", "scheduler"],
        AGG_METRIC_KEYS,
    )
    _write_csv(out_dir / f"{name}_summary.csv", agg_sum)

    pc_sum = _aggregate(
        all_pc,
        ["cards", "arrival_mode", "base_algo", "phase_enabled",
         "scheduler", "card_id", "kind"],
        PERCARD_METRIC_KEYS,
    )
    _write_csv(out_dir / f"{name}_percard_summary.csv", pc_sum)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("experiment",
                        choices=["main4", "main16", "main"],
                        default="main", nargs="?")
    parser.add_argument("--cards", type=int, default=None,
                        help="override card count (default: 4 for main/main4, 16 for main16)")
    parser.add_argument("--tasks", type=int, default=None,
                        help="override task count (default: 800 for 4-card, 3200 for 16-card)")
    parser.add_argument("--bw-max", type=float, default=DEFAULT_BW_MAX,
                        help=f"per-card NoC cap (default: {DEFAULT_BW_MAX:g})")
    parser.add_argument("--d-max", type=int, default=DEFAULT_D_MAX,
                        help=f"Stage-B phase-shift delay budget (default: {DEFAULT_D_MAX})")
    parser.add_argument("--name", default=None,
                        help="output prefix; default: main{cards}_bw{bw_max}_d{d_max}")
    parser.add_argument("--out-dir", default="data/q2")
    parser.add_argument("--workers", type=int, default=0,
                        help="parallel workers; 0 = min(nproc, MemAvail/1.5GB, jobs)")
    args = parser.parse_args()

    cards = args.cards
    tasks = args.tasks
    if cards is None:
        cards = 16 if args.experiment == "main16" else 4
    if tasks is None:
        tasks = 3200 if cards >= 16 else 800
    if args.name is None:
        bw_tag = f"{args.bw_max:g}".replace("+0", "").replace(".", "_")
        name = f"main{cards}_bw{bw_tag}_d{args.d_max}"
    else:
        name = args.name

    out_dir = Path(args.out_dir)
    with tempfile.TemporaryDirectory(prefix="q2_fp_") as tmp:
        fp_dir = _build_mixed_fp_dir(Path(tmp) / "mixed")
        run_matrix(out_dir, name, cards=cards, tasks=tasks,
                   fp_dir=fp_dir, workers=args.workers,
                   bw_max=args.bw_max, d_max=args.d_max)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
