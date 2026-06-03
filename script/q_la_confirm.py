"""Confirmation runs for the best load-aware stps-la configs from the smoke sweep.

Runs 5 seeds x 2 arrivals at full Q0 settings (cards=16, tasks=3200, steps=512)
for the bestfit baseline + anchor `stps` + tuned `stps-la(w_L,w_q,cull)`.

Parallelized via ProcessPoolExecutor; default workers = min(nproc, jobs).
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

from simulation.engine import run_simulation
from script.q0_run import _build_mixed_fp_dir

_root = logging.getLogger()
_root.setLevel(logging.ERROR)
if not _root.handlers:
    _root.addHandler(logging.NullHandler())
for n in ("simulation", "schedule", "util", "fingerprint", "simulation.engine"):
    logging.getLogger(n).setLevel(logging.ERROR)


SEEDS = [21, 42, 99, 123, 2024]
CARDS = 16
TASKS = 3200
STEPS = 512
BW_CAP = 9.0e5

# (label, scheduler, w_L, w_q, cull)
# 3 schedulers per user spec: bestfit baseline, plain stps, tuned stps-la.
# Tuned config (w_L=2.0, w_q=2.0, cull=0.75) is the smoke-sweep winner on
# 16-card bursty (max_min -56.7%, throughput +0.31%, cong_ratio -1.8%).
CONFIGS = [
    ("bestfit",                 "bestfit",  0.0, 0.0, 0.0),
    ("stps",                    "stps",     0.0, 0.0, 0.0),
    ("stps-la(2,2,0.75)",       "stps-la",  2.0, 2.0, 0.75),
]


def _ci95(vals):
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


def _run_one(scheduler, seed, arrival, fp_dir, w_L, w_q, cull, label):
    _silence_logging()
    # Per-job data dir avoids file-name collisions across workers.
    job_tag = f"{scheduler}_{arrival}_{seed}_{int(w_L*10)}_{int(w_q*10)}_{int(cull*100)}"
    kwargs = dict(
        scheduler=scheduler, cards=CARDS, tasks=TASKS, steps=STEPS,
        seed=seed, arrival_mode=arrival, fingerprint_dir=fp_dir,
        bw_max=BW_CAP, bw_cap=BW_CAP, d_max=2, horizon=64,
        centrality_split_threshold=0.2,
        log_dir="log", data_dir=f"data/q0/_confirm_raw/{job_tag}",
    )
    if scheduler in ("stps", "stps-la"):
        kwargs.update(load_weight=w_L, backlog_weight=w_q, stage1_cull_frac=cull)
    m = run_simulation(**kwargs)
    return {
        "label": label, "scheduler": scheduler, "w_L": w_L, "w_q": w_q, "cull": cull,
        "arrival": arrival, "seed": seed,
        "card_cv": m.avg_card_cv, "card_jfi": m.avg_card_jfi,
        "card_lif": m.avg_card_lif, "max_min_ratio": m.avg_max_min_ratio,
        "throughput": m.throughput, "p99_delay": m.p99_delay,
        "cong_ratio": m.avg_congestion_ratio,
        "cong_wait": m.mean_congestion_wait_ticks,
        "cong_p95_wait": m.p95_congestion_wait_ticks,
        "time_card_lif_served_mean": m.time_card_lif_served_mean,
        "time_card_lif_served_max": m.time_card_lif_served_max,
        "time_card_lif_demand_mean": m.time_card_lif_demand_mean,
        "time_card_lif_demand_max": m.time_card_lif_demand_max,
        "time_card_cv_demand_mean": m.time_card_cv_demand_mean,
    }


def _write(path: Path, rows: List[Dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fns: List[str] = []
    seen = set()
    for r in rows:
        for k in r:
            if k not in seen:
                seen.add(k); fns.append(k)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fns)
        w.writeheader(); w.writerows(rows)


def _agg(rows):
    buckets: Dict[tuple, List[Dict]] = {}
    for r in rows:
        key = (r["label"], r["arrival"])
        buckets.setdefault(key, []).append(r)
    out = []
    keys = ["card_cv", "card_jfi", "card_lif", "max_min_ratio", "throughput",
            "p99_delay", "cong_ratio", "cong_wait", "cong_p95_wait",
            "time_card_lif_served_mean", "time_card_lif_served_max",
            "time_card_lif_demand_mean", "time_card_lif_demand_max",
            "time_card_cv_demand_mean"]
    for (label, arrival), items in buckets.items():
        agg = {"label": label, "arrival": arrival,
               "scheduler": items[0]["scheduler"],
               "w_L": items[0]["w_L"], "w_q": items[0]["w_q"], "cull": items[0]["cull"],
               "n": len(items)}
        for k in keys:
            vs = [r[k] for r in items]
            agg[f"{k}_mean"] = mean(vs)
            agg[f"{k}_ci95"] = _ci95(vs)
        out.append(agg)
    return out


def _default_workers(n_jobs: int, mem_per_worker_gb: float = 4.0) -> int:
    """Pick a worker count that respects both CPU and RAM.

    Full Q0 runs (16-card / 3200-task / 512-step) peak ~3.7 GB RSS; reserve a
    margin so a 16-core / 16 GB box does not OOM-kill the pool.
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=0,
                    help="parallel workers; 0 = auto (min of nproc, MemAvail/2.5GB, jobs)")
    args = ap.parse_args()

    rows: List[Dict] = []
    with tempfile.TemporaryDirectory(prefix="q0_confirm_fp_") as tmp:
        fp_dir = str(_build_mixed_fp_dir(Path(tmp) / "mixed"))

        jobs = []
        for arrival in ["poisson", "bursty"]:
            for label, sched, w_L, w_q, cull in CONFIGS:
                for seed in SEEDS:
                    jobs.append((sched, seed, arrival, fp_dir, w_L, w_q, cull, label))

        workers = args.workers or _default_workers(len(jobs))
        print(f"[confirm] {len(jobs)} jobs, {workers} workers", flush=True)

        done = 0
        with ProcessPoolExecutor(max_workers=workers) as pool:
            fut_to_job = {pool.submit(_run_one, *j): j for j in jobs}
            for fut in as_completed(fut_to_job):
                j = fut_to_job[fut]
                sched, seed, arrival, _, _, _, _, label = j
                r = fut.result()
                rows.append(r)
                done += 1
                print(f"  [{done:>2}/{len(jobs)}] arrival={arrival:7s} "
                      f"{label:24s} seed={seed}", flush=True)

    out_dir = Path("data/q0")
    _write(out_dir / "la_confirm_raw.csv", rows)
    summary = _agg(rows)
    _write(out_dir / "la_confirm_summary.csv", summary)

    # Print compact table
    print()
    print(f"{'arrival':<8} {'label':<24} {'cv':>14} {'mm':>7} {'thr':>7} {'cong_r':>8} {'cong_w':>7} {'lif_srv':>8} {'lif_dmd':>8}")
    for arrival in ["poisson", "bursty"]:
        for label, *_ in CONFIGS:
            row = next(r for r in summary if r["arrival"] == arrival and r["label"] == label)
            print(f"{arrival:<8} {label:<24} "
                  f"{row['card_cv_mean']:.4f}±{row['card_cv_ci95']:.4f}  "
                  f"{row['max_min_ratio_mean']:5.2f}  "
                  f"{row['throughput_mean']:.3f}  "
                  f"{row['cong_ratio_mean']:.4f}  "
                  f"{row['cong_wait_mean']:.2f}  "
                  f"{row['time_card_lif_served_mean_mean']:8.4f}  "
                  f"{row['time_card_lif_demand_mean_mean']:8.4f}")
        print()


if __name__ == "__main__":
    main()
