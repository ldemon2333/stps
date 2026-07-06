"""Q1 experiment runner: spatial load balancing via Step A.

Orchestrates the three Q1 experiments described in docs/Q1_TODO.md:
- main:  5 schedulers x 5 seeds at fixed utilization (~70%), Poisson arrivals
- sweep: scheduler x utilization {0.3, 0.5, 0.7, 0.85, 0.95}
- mix:   scheduler x fingerprint mix ratio Steady-Flat / Sparse-Bursty
         {100/0, 75/25, 50/50, 25/75, 0/100}

Outputs:
    data/q1/{exp}_raw.csv       — one row per (scheduler, seed, knob) run
    data/q1/{exp}_summary.csv   — aggregated mean / 95% CI across seeds
    figures/q1/main_table.md    — Markdown table for docs / paper
"""
from __future__ import annotations

import argparse
import csv
import logging
import math
import os
import shutil
import tempfile

# Pin BLAS / OpenMP to one thread per process BEFORE numpy is imported (via
# simulation.engine) so the process Pool below does not oversubscribe cores.
for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
           "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ.setdefault(_v, "1")

import multiprocessing as mp
import sys
from pathlib import Path
from statistics import mean, stdev
from typing import Dict, List, Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from simulation.engine import run_simulation

# Default worker count for the process pool; overridable via --workers.
_WORKERS = 8

logging.getLogger().setLevel(logging.ERROR)
for name in ("simulation", "schedule", "util", "fingerprint", "simulation.engine"):
    logging.getLogger(name).setLevel(logging.ERROR)

# Q1 contrast set: each baseline + STPS Step-A only (stps-spatial)
SCHEDULERS = ["rr", "bestfit", "drf", "p2c", "stps-spatial"]
SCHEDULER_LABELS = {
    "rr": "RR",
    "bestfit": "BestFit",
    "drf": "DRF",
    "p2c": "P2C",
    "stps-spatial": "STPS (Step A)",
}
SEEDS = [21, 42, 99, 123, 2024]
# Cluster scale is overridable via Q1_CARDS so the Stage-A sweeps can be run at
# the paper's 16-card main-experiment scale. Task counts scale proportionally so
# per-card utilisation is held fixed relative to the 4-card calibration.
CARDS = int(os.environ.get("Q1_CARDS", "4"))
_SCALE = max(1, CARDS // 4)
STEPS = 512
DEFAULT_TASKS = 800 * _SCALE  # ~70% utilization under cores=512/card.
FINGERPRINT_DIR = "npz"
FLAT_FP = "npz/synthetic_flat.npz"
BURSTY_FP = "npz/synthetic_bursty.npz"


def _ci95(vals: Sequence[float]) -> float:
    """Half-width of the 95% confidence interval (assuming normal sampling)."""
    n = len(vals)
    if n < 2:
        return 0.0
    return 1.96 * stdev(vals) / math.sqrt(n)


def _run_one(
    scheduler: str,
    seed: int,
    tasks: int,
    arrival_mode: str,
    fingerprint_dir: str = FINGERPRINT_DIR,
) -> Dict:
    metrics = run_simulation(
        scheduler=scheduler,
        cards=CARDS,
        tasks=tasks,
        steps=STEPS,
        seed=seed,
        arrival_mode=arrival_mode,
        fingerprint_dir=fingerprint_dir,
        bw_max=5e6,
        d_max=16,
        horizon=64,
        centrality_split_threshold=0.2,
        log_dir="log",
        data_dir="data/q1/_raw",
    )
    return {
        "scheduler": scheduler,
        "seed": seed,
        "tasks": tasks,
        "arrival_mode": arrival_mode,
        "card_cv": metrics.avg_card_cv,
        "card_jfi": metrics.avg_card_jfi,
        "max_min_ratio": metrics.avg_max_min_ratio,
        "avg_load_imbalance": metrics.avg_load_imbalance,
        "completion_rate": metrics.completion_rate,
        "throughput": metrics.throughput,
    }


def _run_job(job: Dict) -> Dict:
    """Pool worker: run one simulation and merge any extra knob fields back in."""
    row = _run_one(
        job["scheduler"], job["seed"], job["tasks"], job["arrival_mode"],
        fingerprint_dir=job.get("fingerprint_dir", FINGERPRINT_DIR),
    )
    for k in ("utilization", "flat_pct", "bursty_pct"):
        if k in job:
            row[k] = job[k]
    return row


def _run_jobs_parallel(jobs: List[Dict], workers: int) -> List[Dict]:
    """Run a list of job specs across a spawn-based process Pool, preserving order."""
    if workers <= 1 or len(jobs) <= 1:
        return [_run_job(j) for j in jobs]
    ctx = mp.get_context("spawn")
    with ctx.Pool(processes=workers, maxtasksperchild=1) as pool:
        return list(pool.map(_run_job, jobs))


def _write_csv(path: Path, rows: List[Dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        return
    # Union of keys across rows so heterogeneous experiment tables write cleanly.
    fieldnames: List[str] = []
    seen = set()
    for r in rows:
        for k in r:
            if k not in seen:
                seen.add(k)
                fieldnames.append(k)
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _aggregate(rows: List[Dict], group_keys: Sequence[str]) -> List[Dict]:
    """Group rows by `group_keys`, return mean and 95% CI half-width per metric."""
    metric_keys = [
        "card_cv", "card_jfi", "max_min_ratio",
        "avg_load_imbalance", "completion_rate", "throughput",
    ]
    buckets: Dict[tuple, List[Dict]] = {}
    for row in rows:
        key = tuple(row[k] for k in group_keys)
        buckets.setdefault(key, []).append(row)
    out = []
    for key, items in buckets.items():
        agg = {k: v for k, v in zip(group_keys, key)}
        agg["n"] = len(items)
        for m in metric_keys:
            vals = [r[m] for r in items]
            agg[f"{m}_mean"] = mean(vals)
            agg[f"{m}_ci95"] = _ci95(vals)
        out.append(agg)
    return out


def run_main(out_dir: Path, workers: int = _WORKERS) -> List[Dict]:
    print(f"[Q1.main] {len(SCHEDULERS)} schedulers x {len(SEEDS)} seeds, "
          f"tasks={DEFAULT_TASKS}, steps={STEPS}, arrival=poisson, workers={workers}")
    jobs = [
        {"scheduler": sched, "seed": seed, "tasks": DEFAULT_TASKS,
         "arrival_mode": "poisson"}
        for sched in SCHEDULERS for seed in SEEDS
    ]
    rows = _run_jobs_parallel(jobs, workers)
    _write_csv(out_dir / "main_raw.csv", rows)
    summary = _aggregate(rows, ["scheduler"])
    _write_csv(out_dir / "main_summary.csv", summary)
    return summary


def run_sweep(out_dir: Path, workers: int = _WORKERS) -> List[Dict]:
    # Utilization knobs: vary `tasks` while holding everything else fixed.
    util_to_tasks = {k: v * _SCALE for k, v in
                     {0.30: 320, 0.50: 560, 0.70: 800, 0.85: 1020, 0.95: 1100}.items()}
    print(f"[Q1.sweep] {len(SCHEDULERS)} schedulers x {len(util_to_tasks)} util x "
          f"{len(SEEDS)} seeds, arrival=poisson, workers={workers}")
    jobs = [
        {"scheduler": sched, "seed": seed, "tasks": tasks,
         "arrival_mode": "poisson", "utilization": util}
        for sched in SCHEDULERS
        for util, tasks in util_to_tasks.items()
        for seed in SEEDS
    ]
    rows = _run_jobs_parallel(jobs, workers)
    _write_csv(out_dir / "sweep_raw.csv", rows)
    summary = _aggregate(rows, ["scheduler", "utilization"])
    _write_csv(out_dir / "sweep_summary.csv", summary)
    return summary


def _build_mix_fingerprint_dir(parent: Path, n_flat: int, n_bursty: int) -> Path:
    """Materialize a fingerprint directory with `n_flat` copies of synthetic_flat.npz
    and `n_bursty` copies of synthetic_bursty.npz, alternated.

    The simulator assigns task -> fingerprint by `task_id % len(paths)`, so the
    proportion in the directory is what controls the realized topology mix.
    """
    parent.mkdir(parents=True, exist_ok=True)
    # Interleave so any prefix is balanced; then sorted() in engine restores order.
    written = 0
    flat_left, bursty_left = n_flat, n_bursty
    while flat_left > 0 or bursty_left > 0:
        if flat_left > 0:
            shutil.copy(FLAT_FP, parent / f"fp_{written:04d}_flat.npz")
            written += 1
            flat_left -= 1
        if bursty_left > 0:
            shutil.copy(BURSTY_FP, parent / f"fp_{written:04d}_bursty.npz")
            written += 1
            bursty_left -= 1
    return parent


def run_mix(out_dir: Path, workers: int = _WORKERS) -> List[Dict]:
    """Real fingerprint-mix ratio sweep, controlling the Steady-Flat / Sparse-Bursty proportion."""
    ratios = [(100, 0), (75, 25), (50, 50), (25, 75), (0, 100)]
    print(f"[Q1.mix] {len(SCHEDULERS)} schedulers x {len(ratios)} mix ratios x "
          f"{len(SEEDS)} seeds, arrival=poisson, tasks={DEFAULT_TASKS}, workers={workers}")

    rows: List[Dict] = []
    with tempfile.TemporaryDirectory(prefix="q1_mix_fp_") as tmp_root:
        tmp_root_p = Path(tmp_root)
        # Pre-materialize one fingerprint dir per ratio.
        ratio_dirs: Dict[tuple, str] = {}
        for flat_pct, bursty_pct in ratios:
            d = tmp_root_p / f"flat{flat_pct}_bursty{bursty_pct}"
            # Fingerprint-dir size scales with cards to keep the round-robin
            # period aligned with the cluster's per-card assignment.
            n_flat = flat_pct * CARDS // 100
            n_bursty = bursty_pct * CARDS // 100
            # Edge cases (100/0 or 0/100): degenerate to a single-file dir.
            if n_flat == 0 and n_bursty == 0:
                # Should not happen with these ratios, but guard.
                n_bursty = 1
            _build_mix_fingerprint_dir(d, n_flat, n_bursty)
            ratio_dirs[(flat_pct, bursty_pct)] = str(d)

        jobs = [
            {"scheduler": sched, "seed": seed, "tasks": DEFAULT_TASKS,
             "arrival_mode": "poisson",
             "fingerprint_dir": ratio_dirs[(flat_pct, bursty_pct)],
             "flat_pct": flat_pct, "bursty_pct": bursty_pct}
            for sched in SCHEDULERS
            for flat_pct, bursty_pct in ratios
            for seed in SEEDS
        ]
        # Run inside the TemporaryDirectory context so worker processes can
        # still read the per-ratio fingerprint dirs.
        rows = _run_jobs_parallel(jobs, workers)

    _write_csv(out_dir / "mix_raw.csv", rows)
    summary = _aggregate(rows, ["scheduler", "flat_pct", "bursty_pct"])
    _write_csv(out_dir / "mix_summary.csv", summary)
    return summary


def write_main_markdown(summary: List[Dict], out: Path) -> None:
    by_sched = {r["scheduler"]: r for r in summary}
    rr = by_sched.get("rr")
    bf = by_sched.get("bestfit")
    stps = by_sched.get("stps-spatial")

    lines = [
        "# Q1 Main Table — Spatial Load Balancing via Step A",
        "",
        f"Setup: cards={CARDS} × 512 cores = 2048 cores, tasks={DEFAULT_TASKS}, "
        f"steps={STEPS}, arrival=poisson, seeds={SEEDS}",
        "",
        "Values are `mean ± 95% CI half-width` across seeds.",
        "",
        "| Scheduler | card-CV ↓ | card-JFI ↑ | Max/Min ↓ | avg load var |",
        "|---|---|---|---|---|",
    ]
    for sched in SCHEDULERS:
        r = by_sched.get(sched)
        if r is None:
            continue
        label = SCHEDULER_LABELS[sched]
        lines.append(
            f"| {label} | "
            f"{r['card_cv_mean']:.4f} ± {r['card_cv_ci95']:.4f} | "
            f"{r['card_jfi_mean']:.4f} ± {r['card_jfi_ci95']:.4f} | "
            f"{r['max_min_ratio_mean']:.3f} | "
            f"{r['avg_load_imbalance_mean']:.1f} |"
        )

    if rr and bf and stps and rr["card_cv_mean"] > 0 and bf["card_cv_mean"] > 0:
        cv_drop_rr = (rr["card_cv_mean"] - stps["card_cv_mean"]) / rr["card_cv_mean"]
        cv_drop_bf = (bf["card_cv_mean"] - stps["card_cv_mean"]) / bf["card_cv_mean"]
        lines += [
            "",
            "## Headline Numbers",
            "",
            f"- STPS card-CV vs RR:      **{cv_drop_rr*100:+.1f}%** "
            f"(paper claim: −20.1%)",
            f"- STPS card-CV vs BestFit: **{cv_drop_bf*100:+.1f}%** "
            f"(paper claim: −11.2%)",
            f"- P2C card-JFI:            **{by_sched['p2c']['card_jfi_mean']:.3f}** "
            f"(paper claim: ≈0.67)",
        ]

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n")
    print(f"[Q1] wrote {out}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("experiment", choices=["main", "sweep", "mix", "all"])
    ap.add_argument("--out-dir", default="data/q1")
    ap.add_argument("--workers", type=int, default=_WORKERS,
                    help=f"parallel worker processes (default: {_WORKERS})")
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    if args.experiment in ("main", "all"):
        summary = run_main(out_dir, workers=args.workers)
        write_main_markdown(summary, Path("figures/q1/main_table.md"))
    if args.experiment in ("sweep", "all"):
        run_sweep(out_dir, workers=args.workers)
    if args.experiment in ("mix", "all"):
        run_mix(out_dir, workers=args.workers)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
