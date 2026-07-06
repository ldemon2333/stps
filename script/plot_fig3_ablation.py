#!/usr/bin/env python3
"""FIG-3: incremental stage ablation (Stage A + Stage B complementarity).

Per arrival, four bars: the capacity baseline (BestFit), the same baseline with
phase-shift alone (BestFit+ps), STPS macro-dispatch alone (Stage A,
stps-spatial), and full STPS (Stage A + Stage B). Bars are the NoC congestion
ratio; each bar is annotated with its throughput. The message: phase-shift on a
blind baseline zeroes congestion only by paying a large throughput cost;
macro-dispatch alone preserves throughput but leaves congestion untouched; only
the pairing (full STPS) relieves congestion at preserved throughput.

Reads data/q2/main16_bw9e5_d2_summary.csv (the tab:q2_phase source, 16 cards).
Writes SNN schedule/picture/fig_ablation.pdf.
"""
from __future__ import annotations

import csv
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]
PIC = ROOT / "SNN schedule" / "picture"
SUMMARY = ROOT / "data/q2/main16_bw9e5_d2_summary.csv"

# (base_algo, phase_enabled) -> label, colour
STEPS = [
    ("BestFit", False, "BestFit\n(baseline)", "#2ca02c"),
    ("BestFit", True, "BestFit+ps\n(Stage B only)", "#8c9e3a"),
    ("STPS-spatial", False, "STPS-A\n(Stage A only)", "#ff7f0e"),
    ("STPS-spatial", True, "STPS\n(A+B)", "#d62728"),
]
ARRIVALS = ["poisson", "bursty"]


def _load():
    d = {}
    for r in csv.DictReader(SUMMARY.open()):
        key = (r["arrival_mode"], r["base_algo"], r["phase_enabled"].lower() == "true")
        d[key] = r
    return d


def main() -> int:
    PIC.mkdir(parents=True, exist_ok=True)
    d = _load()
    fig, axes = plt.subplots(1, 2, figsize=(8.4, 3.2), sharey=True)
    for ax, arr in zip(axes, ARRIVALS):
        congs, tputs, labels, colors = [], [], [], []
        for base, ph, lab, col in STEPS:
            row = d[(arr, base, ph)]
            congs.append(float(row["avg_congestion_ratio_mean"]))
            tputs.append(float(row["throughput_mean"]))
            labels.append(lab)
            colors.append(col)
        x = np.arange(len(STEPS))
        bars = ax.bar(x, congs, 0.62, color=colors, edgecolor="black", linewidth=0.4)
        base_t = tputs[0]
        for xi, (c, t) in enumerate(zip(congs, tputs)):
            ax.text(xi, c + 0.0015, f"tput\n{t:.3f}", ha="center", va="bottom", fontsize=7)
            drop = (t - base_t) / base_t * 100
            if xi > 0:
                ax.text(xi, -0.006, f"{drop:+.0f}%", ha="center", va="top",
                        fontsize=7, color=("#b00" if drop < -3 else "#333"))
        ax.set_xticks(x)
        ax.set_xticklabels(labels, fontsize=7.5)
        ax.set_title(arr.capitalize(), fontsize=10)
        ax.grid(True, axis="y", alpha=0.3)
        ax.set_ylim(-0.014, max(congs) * 1.45)
    axes[0].set_ylabel("NoC congestion ratio")
    fig.suptitle("Stage ablation: only A+B relieves congestion at preserved throughput",
                 fontsize=10, y=1.02)
    fig.tight_layout()
    out = PIC / "fig_ablation.pdf"
    fig.savefig(out, bbox_inches="tight")
    fig.savefig(out.with_suffix(".png"), dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"saved {out}")
    for arr in ARRIVALS:
        print(f"  {arr}:")
        for base, ph, lab, _ in STEPS:
            row = d[(arr, base, ph)]
            print(f"    {lab.splitlines()[0]:12s} cong={float(row['avg_congestion_ratio_mean']):.4f} "
                  f"tput={float(row['throughput_mean']):.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
