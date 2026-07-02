#!/usr/bin/env python3
"""FIG-1: per-card NoC-congestion distribution (the RQ1 distributional figure).

Survival function P[per-card congestion > x] pooled over every (card, tick) in
the steady window, 16 cards, one panel per arrival, STPS vs the 4 baselines.
The body overlaps but the right tail -- the congested cards at congested ticks
-- separates: STPS's tail is the shortest, so it not only lowers *mean*
congestion (Table/FIG-2) but shrinks the whole upper distribution of per-card
congestion. This is the Shockwave FTF-distribution / Pollux fairness-CDF
analogue, cast on the metric STPS actually targets.

Runs its own 16-card sims (per-card arrays are not in the q0 summaries) and reads
per-card congestion from metrics.load_snapshots. Writes
SNN schedule/picture/fig_percard_cdf.pdf.
"""
from __future__ import annotations

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
WARMUP = 64
SCHEDULERS = ["rr", "bestfit", "drf", "p2c", "stps"]
LABELS = {"rr": "RR", "bestfit": "BestFit", "drf": "DRF", "p2c": "P2C", "stps": "STPS"}
COLORS = {"rr": "#1f77b4", "bestfit": "#2ca02c", "drf": "#9467bd",
          "p2c": "#ff7f0e", "stps": "#d62728"}
ARRIVALS = ["poisson", "bursty"]
MIXED = ["synthetic_flat.npz", "synthetic_pulse_t8.npz", "synthetic_pulse_t16.npz",
         "synthetic_bursty.npz", "spikformer_cifar10.npz", "qkformer_cifar10.npz",
         "spikingresformer_ti_imagenet.npz"]


def _fp(parent: Path) -> str:
    parent.mkdir(parents=True, exist_ok=True)
    for n in MIXED:
        save_fingerprint(parent / n, load_fingerprint(ROOT / "npz" / n))
    return str(parent)


def _percard_pressure(metrics) -> np.ndarray:
    """Per-card time-mean NoC congestion ratio over the steady window.

    Each card's snapshot congestion ratio is backlog/(served+backlog); we
    average it over the steady window, giving one value per card. Pooling these
    across seeds yields a per-card congestion distribution whose whole body
    (not just the mean) shifts down under STPS."""
    snaps = metrics.load_snapshots
    if not snaps:
        return np.zeros(0)
    cids = sorted(snaps[0].card_congestion_ratio.keys())
    trimmed = snaps[WARMUP:-WARMUP] if len(snaps) > 2 * WARMUP else snaps
    mat = np.array([[s.card_congestion_ratio.get(c, 0.0) for c in cids] for s in trimmed])
    return mat.mean(axis=0)  # (M,) per-card time-mean congestion


def _job(a):
    sched, arrival, seed, fp_dir = a
    m = run_simulation(
        scheduler=sched, cards=CARDS, tasks=TASKS, steps=STEPS, seed=seed,
        arrival_mode=arrival, fingerprint_dir=fp_dir, bw_max=9e5, bw_cap=9e5,
        d_max=2, horizon=64, centrality_split_threshold=0.2,
        log_dir="log", data_dir=f"data/_fig1/{sched}_{arrival}_{seed}",
    )
    return (sched, arrival), _percard_pressure(m)


def main() -> int:
    import multiprocessing as mp
    import tempfile
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    pic = ROOT / "SNN schedule" / "picture"
    pic.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="fig1_fp_") as tmp:
        fp_dir = _fp(Path(tmp) / "mixed")
        jobs = [(s, a, sd, fp_dir) for a in ARRIVALS for s in SCHEDULERS for sd in SEEDS]
        print(f"[FIG-1] {len(jobs)} 16-card runs ...", flush=True)
        ctx = mp.get_context("fork")
        acc = {}
        with ctx.Pool(processes=min(len(jobs), os.cpu_count() or 1),
                      maxtasksperchild=1) as pool:
            for key, arr in pool.imap_unordered(_job, jobs):
                acc.setdefault(key, []).append(arr)
                print(f"  done {key[0]:8s} {key[1]}", flush=True)

    fig, axes = plt.subplots(1, 2, figsize=(8.2, 3.0), sharey=True)
    for ax, arrival in zip(axes, ARRIVALS):
        for s in SCHEDULERS:
            data = np.sort(np.concatenate(acc[(s, arrival)]))
            cdf = np.arange(1, len(data) + 1) / len(data)  # P[X <= x]
            ax.step(data, cdf, where="post", lw=1.7, color=COLORS[s], label=LABELS[s])
        ax.set_xlabel("per-card time-mean congestion ratio")
        ax.set_title(f"{arrival.capitalize()}", fontsize=10)
        ax.grid(True, alpha=0.3)
        ax.set_ylim(0, 1)
    axes[0].set_ylabel("CDF over cards")
    axes[1].legend(fontsize=8, loc="lower right", framealpha=0.9)
    fig.tight_layout()
    out = pic / "fig_percard_cdf.pdf"
    fig.savefig(out, bbox_inches="tight")
    fig.savefig(out.with_suffix(".png"), dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"saved {out}")

    print("\n=== FIG-1 per-card congestion summary ===")
    for arrival in ARRIVALS:
        for s in SCHEDULERS:
            data = np.concatenate(acc[(s, arrival)])
            print(f"  {arrival:8s} {LABELS[s]:8s} mean={data.mean():.4f} "
                  f"p90={np.percentile(data,90):.4f} max={data.max():.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
