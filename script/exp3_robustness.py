#!/usr/bin/env python3
"""EXP-3 (robustness): STPS under fingerprint / forecast calibration drift.

STPS relies on offline fingerprints. We perturb the scheduler's *view* of each
task's traffic timeline and burstiness by multiplicative +/-{0,10,25,50}% noise
(the true simulated traffic is unchanged), modelling deployment-vs-calibration
drift, and measure how congestion and worst-card pressure degrade. The best
capacity baseline (BestFit) is fingerprint-blind, so it is a flat reference:
STPS should stay at or below it until noise is large, then converge to it
(never worse).

16 cards, bursty, 5 seeds. Writes data/robustness_summary.csv and
SNN schedule/picture/fig_robustness.pdf.
"""
from __future__ import annotations

import csv
import os
import sys
from pathlib import Path

for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
           "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ.setdefault(_v, "1")

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import logging
_root = logging.getLogger()
_root.setLevel(logging.ERROR)
if not _root.handlers:
    _root.addHandler(logging.NullHandler())
for _n in ("simulation", "schedule", "util", "fingerprint", "simulation.engine"):
    logging.getLogger(_n).setLevel(logging.ERROR)

from fingerprint import load_fingerprint, save_fingerprint
from simulation.engine import run_simulation

CARDS = 16
TASKS = 3200
STEPS = 512
SEEDS = [21, 42, 99, 123, 2024]
NOISE = [0.0, 0.10, 0.25, 0.50]
MIXED = ["synthetic_flat.npz", "synthetic_pulse_t8.npz", "synthetic_pulse_t16.npz",
         "synthetic_bursty.npz", "spikformer_cifar10.npz", "qkformer_cifar10.npz",
         "spikingresformer_ti_imagenet.npz"]


def _fp(parent: Path) -> str:
    parent.mkdir(parents=True, exist_ok=True)
    for n in MIXED:
        save_fingerprint(parent / n, load_fingerprint(ROOT / "npz" / n))
    return str(parent)


def _job(a):
    kind, noise, seed, fp_dir = a
    kw = dict(cards=CARDS, tasks=TASKS, steps=STEPS, seed=seed, arrival_mode="bursty",
              fingerprint_dir=fp_dir, bw_max=9e5, bw_cap=9e5, d_max=2, horizon=64,
              centrality_split_threshold=0.2, log_dir="log",
              data_dir=f"data/_exp3/{kind}_{noise}_{seed}")
    if kind == "stps":
        kw.update(scheduler="stps", fingerprint_noise=noise, fingerprint_noise_seed=seed)
    else:
        kw.update(scheduler="bestfit")
    m = run_simulation(**kw)
    return (kind, noise, seed), (m.avg_congestion_ratio, m.avg_max_min_ratio,
                                 m.mean_congestion_wait_ticks)


def main() -> int:
    import multiprocessing as mp
    import tempfile
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    pic = ROOT / "SNN schedule" / "picture"
    pic.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="exp3_fp_") as tmp:
        fp_dir = _fp(Path(tmp) / "mixed")
        jobs = [("stps", n, s, fp_dir) for n in NOISE for s in SEEDS]
        jobs += [("bestfit", 0.0, s, fp_dir) for s in SEEDS]  # flat reference
        print(f"[EXP-3] {len(jobs)} runs (STPS x {len(NOISE)} noise + BestFit ref) ...",
              flush=True)
        ctx = mp.get_context("fork")
        res = {}
        with ctx.Pool(processes=min(len(jobs), os.cpu_count() or 1),
                      maxtasksperchild=1) as pool:
            for key, val in pool.imap_unordered(_job, jobs):
                res[key] = val
                print(f"  done {key}", flush=True)

    # Aggregate.
    def agg(kind, noise, idx):
        vals = [res[(kind, noise, s)][idx] for s in SEEDS]
        return float(np.mean(vals)), 1.96 * float(np.std(vals)) / np.sqrt(len(vals))

    rows = []
    for n in NOISE:
        cong, cong_ci = agg("stps", n, 0)
        mm, mm_ci = agg("stps", n, 1)
        rows.append({"noise": n, "cong": cong, "cong_ci": cong_ci,
                     "maxmin": mm, "maxmin_ci": mm_ci})
    base_cong, base_cong_ci = agg("bestfit", 0.0, 0)
    base_mm, base_mm_ci = agg("bestfit", 0.0, 1)

    with (ROOT / "data" / "robustness_summary.csv").open("w", newline="") as h:
        w = csv.writer(h)
        w.writerow(["noise", "stps_cong", "stps_cong_ci95", "stps_maxmin", "stps_maxmin_ci95"])
        for r in rows:
            w.writerow([r["noise"], f"{r['cong']:.5f}", f"{r['cong_ci']:.5f}",
                        f"{r['maxmin']:.4f}", f"{r['maxmin_ci']:.4f}"])
        w.writerow(["bestfit_ref", f"{base_cong:.5f}", f"{base_cong_ci:.5f}",
                    f"{base_mm:.4f}", f"{base_mm_ci:.4f}"])

    # ---- Figure: congestion ratio vs noise, STPS line + BestFit reference band ----
    x = [n * 100 for n in NOISE]
    fig, ax = plt.subplots(figsize=(4.4, 2.9))
    ax.errorbar(x, [r["cong"] for r in rows], yerr=[r["cong_ci"] for r in rows],
                marker="o", color="#d62728", capsize=3, lw=1.6, label="STPS")
    ax.axhline(base_cong, color="#2ca02c", ls="--", lw=1.3, label="BestFit (blind)")
    ax.fill_between([min(x), max(x)], base_cong - base_cong_ci, base_cong + base_cong_ci,
                    color="#2ca02c", alpha=0.15)
    ax.set_xlabel("fingerprint noise (% multiplicative)")
    ax.set_ylabel("NoC congestion ratio")
    ax.legend(fontsize=8, loc="best")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    out = pic / "fig_robustness.pdf"
    fig.savefig(out, bbox_inches="tight")
    fig.savefig(out.with_suffix(".png"), dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"saved {out}")

    print("\n=== EXP-3 summary ===")
    for r in rows:
        print(f"  noise={r['noise']*100:3.0f}%  cong={r['cong']:.4f}+-{r['cong_ci']:.4f}  "
              f"maxmin={r['maxmin']:.2f}")
    print(f"  BestFit ref: cong={base_cong:.4f}  maxmin={base_mm:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
