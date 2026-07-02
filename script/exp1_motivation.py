#!/usr/bin/env python3
"""EXP-1 (motivation): quantify the per-card NoC-congestion pathology.

A capacity-balanced scheduler (BestFit) that looks evenly packed by *core count*
still saturates individual cards' NoC when bursty tenants co-locate, because the
mean hides the instantaneous peaks. We run BestFit at 16 cards, bursty arrivals,
*uncapped* (so the recorded per-card demand is the true injection need, not the
clipped served rate), and measure:

  (a) per-card instantaneous injection over time vs the p75 cap line
      -> figure picture/motivation_congestion.pdf
  (b) fraction of steady-state ticks the busiest card exceeds the cap
  (c) worst-card / mean NoC pressure as a function of mean utilisation
      -> figure picture/motivation_pressure_vs_util.pdf

Outputs the two PDFs plus data/motivation_summary.csv and prints the headline
sentence numbers for the paper's Motivation paragraph.
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
STEPS = 512
SEEDS = [21, 42, 99, 123, 2024]
WARMUP = 64  # steady-state window: drop first/last WARMUP ticks
BW_CAP_REF = 9.0e5  # p75-derived reference cap used elsewhere in the paper
UNCAPPED = 5.0e6    # high enough that demand never clips -> served == demand
MIXED_FINGERPRINTS = [
    "synthetic_flat.npz", "synthetic_pulse_t8.npz", "synthetic_pulse_t16.npz",
    "synthetic_bursty.npz", "spikformer_cifar10.npz", "qkformer_cifar10.npz",
    "spikingresformer_ti_imagenet.npz",
]
# Utilisation sweep: task counts scaled so mean per-card load varies.
UTIL_TASKS = [1600, 2400, 3200, 4000, 4800]


def _build_mixed_fp_dir(parent: Path) -> str:
    parent.mkdir(parents=True, exist_ok=True)
    for name in MIXED_FINGERPRINTS:
        src = ROOT / "npz" / name
        if not src.exists():
            raise FileNotFoundError(f"missing fingerprint {src}")
        save_fingerprint(parent / name, load_fingerprint(src))
    return str(parent)


def _demand_matrix(metrics) -> np.ndarray:
    """(T, M) per-card pre-cap demand over the steady-state window."""
    snaps = metrics.load_snapshots
    if not snaps:
        return np.zeros((0, CARDS))
    cids = sorted(snaps[0].card_demand.keys())
    mat = np.array([[s.card_demand.get(c, 0.0) for c in cids] for s in snaps])
    if mat.shape[0] > 2 * WARMUP:
        mat = mat[WARMUP:-WARMUP]
    return mat


def run_case(scheduler: str, tasks: int, seed: int, bw: float, fp_dir: str):
    m = run_simulation(
        scheduler=scheduler, cards=CARDS, tasks=tasks, steps=STEPS, seed=seed,
        arrival_mode="bursty", fingerprint_dir=fp_dir, bw_max=bw, bw_cap=bw,
        d_max=2, horizon=64, centrality_split_threshold=0.2,
        log_dir="log", data_dir=f"data/_exp1/{scheduler}_{tasks}_{seed}",
    )
    return _demand_matrix(m)


def _job(args):
    tasks, seed, fp_dir = args
    return (tasks, seed), run_case("bestfit", tasks, seed, UNCAPPED, fp_dir)


def _run_all(fp_dir: str):
    """Run every (tasks, seed) BestFit case in parallel; return {(tasks,seed): mat}."""
    import multiprocessing as mp
    jobs = [(tk, s, fp_dir) for tk in ([3200] + UTIL_TASKS) for s in SEEDS]
    seen, uniq = set(), []
    for j in jobs:
        k = (j[0], j[1])
        if k not in seen:
            seen.add(k)
            uniq.append(j)
    ctx = mp.get_context("fork")
    out = {}
    with ctx.Pool(processes=min(len(uniq), os.cpu_count() or 1),
                  maxtasksperchild=1) as pool:
        for key, mat in pool.imap_unordered(_job, uniq):
            out[key] = mat
    return out


def main() -> int:
    import tempfile
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    pic = ROOT / "SNN schedule" / "picture"
    pic.mkdir(parents=True, exist_ok=True)
    data_dir = ROOT / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="exp1_fp_") as tmp:
        fp_dir = _build_mixed_fp_dir(Path(tmp) / "mixed")

        print("[EXP-1] running BestFit 16-card bursty cases (parallel) ...", flush=True)
        cases = _run_all(fp_dir)

        # ---- (a)+(b): time-series at the default operating point (3200 tasks) ----
        rep = cases[(3200, SEEDS[0])]  # representative seed
        t = np.arange(rep.shape[0])
        max_card = rep.max(axis=1)
        mean_card = rep.mean(axis=1)

        # cap = p75 of per-card demand across all (tick, card) samples in the run
        cap = float(np.percentile(rep, 75))

        # (b) over all seeds: fraction of ticks busiest card exceeds cap
        exceed_fracs, pressure_ratios = [], []
        for s in SEEDS:
            mat = cases[(3200, s)]
            capm = float(np.percentile(mat, 75))
            exceed_fracs.append(float((mat.max(axis=1) > capm).mean()))
            pressure_ratios.append(float((mat.max(axis=1) / np.maximum(mat.mean(axis=1), 1e-9)).mean()))
        exceed_mean = float(np.mean(exceed_fracs))
        pressure_mean = float(np.mean(pressure_ratios))

        # ---- Figure A: injection time-series vs cap ----
        fig, ax = plt.subplots(figsize=(7.0, 2.8))
        ax.plot(t, max_card / 1e6, color="#d62728", lw=1.1, label="busiest card")
        ax.plot(t, mean_card / 1e6, color="#1f77b4", lw=1.1, label="cluster mean")
        ax.axhline(cap / 1e6, color="black", ls="--", lw=1.0,
                   label=r"NoC cap ($p75$)")
        ax.fill_between(t, cap / 1e6, max_card / 1e6,
                        where=(max_card > cap), color="#d62728", alpha=0.18)
        ax.set_xlabel("time (ticks)")
        ax.set_ylabel(r"NoC injection ($10^6$/tick)")
        ax.set_title("Capacity-balanced BestFit, 16 cards, bursty (uncapped)")
        ax.legend(loc="upper right", fontsize=8, ncol=3, framealpha=0.9)
        ax.margins(x=0)
        fig.tight_layout()
        fa = pic / "motivation_congestion.pdf"
        fig.savefig(fa, bbox_inches="tight")
        fig.savefig(fa.with_suffix(".png"), dpi=150, bbox_inches="tight")
        plt.close(fig)
        print(f"  saved {fa}")

        # ---- (c): worst-card/mean pressure vs mean utilisation ----
        util_x, press_y, press_ci = [], [], []
        for tk in UTIL_TASKS:
            ratios, utils = [], []
            for s in SEEDS:
                mat = cases[(tk, s)]
                ratios.append(float((mat.max(axis=1) / np.maximum(mat.mean(axis=1), 1e-9)).mean()))
                utils.append(float(mat.mean() / BW_CAP_REF))
            util_x.append(float(np.mean(utils)))
            press_y.append(float(np.mean(ratios)))
            press_ci.append(1.96 * float(np.std(ratios)) / np.sqrt(len(ratios)))

        fig, ax = plt.subplots(figsize=(4.2, 2.8))
        ax.errorbar([u * 100 for u in util_x], press_y, yerr=press_ci,
                    marker="o", color="#d62728", capsize=3, lw=1.4)
        ax.set_xlabel("mean per-card utilisation (%)")
        ax.set_ylabel("worst-card / mean pressure")
        ax.grid(True, alpha=0.3)
        fig.tight_layout()
        fb = pic / "motivation_pressure_vs_util.pdf"
        fig.savefig(fb, bbox_inches="tight")
        fig.savefig(fb.with_suffix(".png"), dpi=150, bbox_inches="tight")
        plt.close(fig)
        print(f"  saved {fb}")

        # ---- summary CSV + headline sentence ----
        with (data_dir / "motivation_summary.csv").open("w", newline="") as h:
            w = csv.writer(h)
            w.writerow(["metric", "value"])
            w.writerow(["exceed_frac_mean", f"{exceed_mean:.4f}"])
            w.writerow(["worst_over_mean_pressure", f"{pressure_mean:.3f}"])
            w.writerow(["cap_p75", f"{cap:.1f}"])
            for u, p in zip(util_x, press_y):
                w.writerow([f"util_{u:.3f}", f"{p:.3f}"])

    print("\n=== EXP-1 headline ===")
    print(f"busiest card exceeds the p75 cap in {exceed_mean*100:.0f}% of steady-state ticks")
    print(f"mean worst-card/mean NoC pressure = {pressure_mean:.1f}x at ~{util_x[UTIL_TASKS.index(3200)]*100:.0f}% util")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
