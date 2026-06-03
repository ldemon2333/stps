"""Weight grid sweep for stps-la (docs/Q0_result.md §7.5 next-step).

Scans (w_L, w_q, cull_top_frac) and reports the delta against the default
`stps` baseline on 16-card bursty + poisson. Smaller seed budget for speed.
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
from script.q0_run import SCALE16_FINGERPRINTS, _build_mixed_fp_dir

_root = logging.getLogger()
_root.setLevel(logging.ERROR)
if not _root.handlers:
    _root.addHandler(logging.NullHandler())
for name in ("simulation", "schedule", "util", "fingerprint", "simulation.engine"):
    logging.getLogger(name).setLevel(logging.ERROR)


SEEDS = [21, 42, 99]
CARDS = 16
TASKS = 3200
STEPS = 512
BW_CAP = 9.0e5
D_MAX = 2
HORIZON = 64


# Smoke-grade overrides for the grid sweep (quick exploration only).
SMOKE_TASKS = 1600
SMOKE_STEPS = 256
SMOKE_SEEDS = [21, 42]


def _ci95(vals: Sequence[float]) -> float:
    n = len(vals)
    if n < 2:
        return 0.0
    return 1.96 * stdev(vals) / math.sqrt(n)


def _silence_logging() -> None:
    r = logging.getLogger()
    r.setLevel(logging.ERROR)
    if not r.handlers:
        r.addHandler(logging.NullHandler())
    for n in ("simulation", "schedule", "util", "fingerprint", "simulation.engine"):
        logging.getLogger(n).setLevel(logging.ERROR)


def _run_one(scheduler: str, seed: int, arrival: str, fp_dir: str,
             w_L: float = 0.0, w_q: float = 0.0, cull: float = 0.0,
             tasks: int = TASKS, steps: int = STEPS) -> Dict:
    _silence_logging()
    job_tag = f"{scheduler}_{arrival}_{seed}_{int(w_L*10)}_{int(w_q*10)}_{int(cull*100)}"
    metrics = run_simulation(
        scheduler=scheduler,
        cards=CARDS,
        tasks=tasks,
        steps=steps,
        seed=seed,
        arrival_mode=arrival,
        fingerprint_dir=fp_dir,
        bw_max=BW_CAP,
        bw_cap=BW_CAP,
        d_max=D_MAX,
        horizon=HORIZON,
        centrality_split_threshold=0.2,
        log_dir="log",
        data_dir=f"data/q0/_sweep_raw/{job_tag}",
        load_weight=w_L,
        backlog_weight=w_q,
        stage1_cull_frac=cull,
    )
    return {
        "scheduler": scheduler,
        "w_L": w_L,
        "w_q": w_q,
        "cull": cull,
        "arrival": arrival,
        "seed": seed,
        "tasks": tasks,
        "steps": steps,
        "card_cv": metrics.avg_card_cv,
        "card_jfi": metrics.avg_card_jfi,
        "max_min_ratio": metrics.avg_max_min_ratio,
        "throughput": metrics.throughput,
        "cong_ratio": metrics.avg_congestion_ratio,
        "cong_wait": metrics.mean_congestion_wait_ticks,
        "p99_delay": metrics.p99_delay,
    }


def _agg(rows: List[Dict], group: Sequence[str]) -> List[Dict]:
    buckets: Dict[tuple, List[Dict]] = {}
    for r in rows:
        key = tuple(r[k] for k in group)
        buckets.setdefault(key, []).append(r)
    out = []
    metric_keys = ["card_cv", "card_jfi", "max_min_ratio", "throughput",
                   "cong_ratio", "cong_wait", "p99_delay"]
    for key, items in buckets.items():
        agg = {k: v for k, v in zip(group, key)}
        agg["n"] = len(items)
        for m in metric_keys:
            vals = [r[m] for r in items]
            agg[f"{m}_mean"] = mean(vals)
            agg[f"{m}_ci95"] = _ci95(vals)
        out.append(agg)
    return out


def _write(path: Path, rows: List[Dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        return
    fieldnames: List[str] = []
    seen = set()
    for r in rows:
        for k in r:
            if k not in seen:
                seen.add(k)
                fieldnames.append(k)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)


def _default_workers(n_jobs: int, mem_per_worker_gb: float = 1.5) -> int:
    """Pick a worker count respecting both CPU and RAM.

    Sweep smoke runs (tasks=1600/steps=256) peak ~1.2 GB/worker; confirm-mode
    runs (tasks=3200/steps=512) peak ~2.2 GB. Default cap assumes smoke mode;
    pass --workers explicitly for confirm-mode sweeps.
    """
    cpu = os.cpu_count() or 1
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    avail_gb = int(line.split()[1]) / (1024 * 1024)
                    break
            else:
                avail_gb = 8.0
    except OSError:
        avail_gb = 8.0
    mem_cap = max(1, int(avail_gb / mem_per_worker_gb))
    return max(1, min(cpu, mem_cap, n_jobs))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default="data/q0")
    ap.add_argument("--arrival", default="bursty", choices=["bursty", "poisson"])
    ap.add_argument("--mode", default="smoke", choices=["smoke", "confirm"],
                    help="smoke: tasks=1600 steps=256 2 seeds. confirm: tasks=3200 steps=512 3 seeds")
    ap.add_argument("--grid", default="wide", choices=["wide", "narrow"])
    ap.add_argument("--workers", type=int, default=0,
                    help="parallel workers; 0 = min(nproc, jobs)")
    args = ap.parse_args()

    if args.mode == "smoke":
        seeds, tasks, steps = SMOKE_SEEDS, SMOKE_TASKS, SMOKE_STEPS
    else:
        seeds, tasks, steps = SEEDS, TASKS, STEPS
    out_dir = Path(args.out_dir)

    # Grid focused on cull (the real lever). w_q kept small because it correlates
    # strongly with w_L in our default formulation.
    grid: List[tuple[float, float, float]] = []
    if args.grid == "wide":
        cull_levels = [0.0, 0.25, 0.5, 0.75]
        wL_levels = [2.0, 4.0, 8.0]
        wq_levels = [0.0, 2.0]
    else:
        cull_levels = [0.5, 0.75]
        wL_levels = [4.0, 8.0]
        wq_levels = [0.0, 2.0]
    for cull in cull_levels:
        for w_L in wL_levels:
            for w_q in wq_levels:
                grid.append((w_L, w_q, cull))

    rows: List[Dict] = []
    with tempfile.TemporaryDirectory(prefix="q0_sweep_fp_") as tmp:
        fp_dir = str(_build_mixed_fp_dir(Path(tmp) / "mixed"))

        print(f"[sweep] mode={args.mode} arrival={args.arrival} "
              f"seeds={seeds} tasks={tasks} steps={steps}")
        print(f"[sweep] grid points = {len(grid)} → total runs = "
              f"{(len(grid) + 1) * len(seeds)}")

        # Build job list: anchor stps + stps-la grid x seeds.
        jobs: List[tuple] = []
        for seed in seeds:
            jobs.append(("stps", seed, args.arrival, fp_dir, 0.0, 0.0, 0.0,
                         tasks, steps))
        for w_L, w_q, cull in grid:
            for seed in seeds:
                jobs.append(("stps-la", seed, args.arrival, fp_dir,
                             w_L, w_q, cull, tasks, steps))

        workers = args.workers or _default_workers(len(jobs))
        print(f"[sweep] {len(jobs)} jobs, {workers} workers")

        done = 0
        with ProcessPoolExecutor(max_workers=workers) as pool:
            fut_to_job = {pool.submit(_run_one, *j): j for j in jobs}
            for fut in as_completed(fut_to_job):
                j = fut_to_job[fut]
                sched, seed, _, _, w_L, w_q, cull, _, _ = j
                row = fut.result()
                rows.append(row)
                done += 1
                if sched == "stps":
                    tag = "anchor stps"
                else:
                    tag = f"stps-la w_L={w_L} w_q={w_q} cull={cull}"
                print(f"  [{done:>3}/{len(jobs)}] {tag} seed={seed}", flush=True)

    tag = f"{args.mode}_{args.arrival}"
    _write(out_dir / f"la_sweep_{tag}_raw.csv", rows)
    summary = _agg(rows, ["scheduler", "w_L", "w_q", "cull"])
    _write(out_dir / f"la_sweep_{tag}_summary.csv", summary)

    # Print top-N improvement vs anchor
    anchor = next(r for r in summary if r["scheduler"] == "stps")
    print(f"\n[anchor stps @ {args.arrival}] "
          f"card_cv={anchor['card_cv_mean']:.4f}, "
          f"max_min={anchor['max_min_ratio_mean']:.2f}, "
          f"throughput={anchor['throughput_mean']:.3f}, "
          f"cong_ratio={anchor['cong_ratio_mean']:.4f}, "
          f"cong_wait={anchor['cong_wait_mean']:.2f}")
    print()
    cand = [r for r in summary if r["scheduler"] == "stps-la"]
    def lift(r):
        cv = (anchor["card_cv_mean"] - r["card_cv_mean"]) / anchor["card_cv_mean"]
        mm = (anchor["max_min_ratio_mean"] - r["max_min_ratio_mean"]) / anchor["max_min_ratio_mean"]
        th = (r["throughput_mean"] - anchor["throughput_mean"]) / anchor["throughput_mean"]
        co = (anchor["cong_ratio_mean"] - r["cong_ratio_mean"]) / anchor["cong_ratio_mean"]
        return cv, mm, th, co
    cand.sort(key=lambda r: -lift(r)[1])  # rank by max_min lift (clearest spread)
    print("All sweep points (ranked by max/min lift vs stps):")
    print(f"  {'w_L':>4} {'w_q':>4} {'cull':>5}  cv_lift   mm_lift   th_lift   co_lift   card_cv  max_min  thrput  cong_r  cong_wait")
    for r in cand:
        cv, mm, th, co = lift(r)
        print(f"  {r['w_L']:>4.1f} {r['w_q']:>4.1f} {r['cull']:>5.2f}  "
              f"{cv*100:+6.2f}%  {mm*100:+6.2f}%  {th*100:+6.2f}%  {co*100:+6.2f}%  "
              f"{r['card_cv_mean']:.4f}  {r['max_min_ratio_mean']:5.2f}  "
              f"{r['throughput_mean']:.3f}  {r['cong_ratio_mean']:.4f}  {r['cong_wait_mean']:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
