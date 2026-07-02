#!/usr/bin/env python3
"""EXP-4 (scalability): worst-card NoC pressure vs cluster size.

The worst-card result is scale-dependent (weak at 4 cards, strong at 16). We
sweep cards in {4,8,16,32,64}, scaling the task count to hold per-card load
roughly constant, and plot worst-card pressure (max/min served load) and
congestion ratio for STPS vs the best baseline at each size. The STPS-vs-best
gap should widen with scale.

Bursty arrivals, 5 seeds. Writes data/scalability_summary.csv and
SNN schedule/picture/fig_scalability.pdf.
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

# Hold per-card task load constant at 200 tasks/card (matches 4c/800, 16c/3200).
CARD_SIZES = [4, 8, 16, 32, 64]
TASKS_PER_CARD = 200
STEPS = 512
SEEDS = [21, 42, 99, 123, 2024]
BASELINES = ["rr", "bestfit", "drf", "p2c"]
MIXED = ["synthetic_flat.npz", "synthetic_pulse_t8.npz", "synthetic_pulse_t16.npz",
         "synthetic_bursty.npz", "spikformer_cifar10.npz", "qkformer_cifar10.npz",
         "spikingresformer_ti_imagenet.npz"]


def _fp(parent: Path) -> str:
    parent.mkdir(parents=True, exist_ok=True)
    for n in MIXED:
        save_fingerprint(parent / n, load_fingerprint(ROOT / "npz" / n))
    return str(parent)


def _job(a):
    sched, cards, seed, fp_dir = a
    m = run_simulation(
        scheduler=sched, cards=cards, tasks=cards * TASKS_PER_CARD, steps=STEPS,
        seed=seed, arrival_mode="bursty", fingerprint_dir=fp_dir,
        bw_max=9e5, bw_cap=9e5, d_max=2, horizon=64, centrality_split_threshold=0.2,
        log_dir="log", data_dir=f"data/_exp4/{sched}_{cards}_{seed}",
    )
    return (sched, cards, seed), (m.avg_max_min_ratio, m.avg_congestion_ratio)


def main() -> int:
    import multiprocessing as mp
    import tempfile
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    pic = ROOT / "SNN schedule" / "picture"
    pic.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="exp4_fp_") as tmp:
        fp_dir = _fp(Path(tmp) / "mixed")
        scheds = BASELINES + ["stps"]
        jobs = [(s, c, sd, fp_dir) for c in CARD_SIZES for s in scheds for sd in SEEDS]
        print(f"[EXP-4] {len(jobs)} runs ({len(scheds)} sched x {len(CARD_SIZES)} sizes "
              f"x {len(SEEDS)} seeds) ...", flush=True)
        ctx = mp.get_context("fork")
        res = {}
        with ctx.Pool(processes=min(len(jobs), os.cpu_count() or 1),
                      maxtasksperchild=1) as pool:
            for key, val in pool.imap_unordered(_job, jobs):
                res[key] = val
                print(f"  done {key[0]:8s} c={key[1]:2d} s={key[2]}", flush=True)

    def agg(sched, cards, idx):
        vals = [res[(sched, cards, s)][idx] for s in SEEDS]
        return float(np.mean(vals)), 1.96 * float(np.std(vals)) / np.sqrt(len(vals))

    # Best baseline per size = lowest congestion ratio (RQ1's primary metric).
    rows = []
    for c in CARD_SIZES:
        stps_cg, stps_cg_ci = agg("stps", c, 1)
        base_vals = {b: agg(b, c, 1) for b in BASELINES}
        best_b = min(base_vals, key=lambda b: base_vals[b][0])
        best_cg, best_cg_ci = base_vals[best_b]
        rows.append({"cards": c, "stps_cong": stps_cg, "stps_cong_ci": stps_cg_ci,
                     "best_baseline": best_b, "best_cong": best_cg,
                     "best_cong_ci": best_cg_ci})

    with (ROOT / "data" / "scalability_summary.csv").open("w", newline="") as h:
        w = csv.DictWriter(h, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    # ---- Figure: congestion ratio vs card count, STPS vs best baseline ----
    x = CARD_SIZES
    fig, ax = plt.subplots(figsize=(4.6, 3.0))
    ax.errorbar(x, [r["stps_cong"] for r in rows],
                yerr=[r["stps_cong_ci"] for r in rows],
                marker="o", color="#d62728", capsize=3, lw=1.7, label="STPS")
    ax.errorbar(x, [r["best_cong"] for r in rows],
                yerr=[r["best_cong_ci"] for r in rows],
                marker="s", color="#1f77b4", capsize=3, lw=1.7, ls="--",
                label="best baseline (per size)")
    ax.set_xscale("log", base=2)
    ax.set_xticks(x)
    ax.set_xticklabels([str(c) for c in x])
    ax.set_xlabel("cluster size (cards)")
    ax.set_ylabel("NoC congestion ratio")
    ax.legend(fontsize=8, loc="best")
    ax.grid(True, alpha=0.3, which="both")
    fig.tight_layout()
    out = pic / "fig_scalability.pdf"
    fig.savefig(out, bbox_inches="tight")
    fig.savefig(out.with_suffix(".png"), dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"saved {out}")

    print("\n=== EXP-4 summary (congestion) ===")
    for r in rows:
        rel = (r["best_cong"] - r["stps_cong"]) / r["best_cong"] * 100
        print(f"  {r['cards']:2d} cards: STPS cong={r['stps_cong']:.4f}  "
              f"best({r['best_baseline']})={r['best_cong']:.4f}  STPS lower by {rel:+.1f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
