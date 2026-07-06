#!/usr/bin/env python3
"""FIG-2: normalized NoC-congestion bar chart (RQ1 centrepiece).

Grouped bars: 6 cells = {4,16} cards x {Poisson, Bursty, Mixed}; 5 scheduler
bars per cell. Congestion ratio is normalized so the *best baseline* in each
cell = 1.0 (dashed line). STPS is the only policy whose bar sits below 1.0 in
every cell; baselines trade leadership. 95% CI error bars.

Reads data/q0/scale16_summary.csv (16-card main experiment).
(16-card). Writes SNN schedule/picture/fig_congestion_bars.pdf.
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

SCHEDULERS = ["rr", "bestfit", "drf", "p2c", "stps"]
LABELS = {"rr": "RR", "bestfit": "BestFit", "drf": "DRF", "p2c": "P2C", "stps": "STPS"}
BASELINES = ["rr", "bestfit", "drf", "p2c"]
ARRIVALS = ["poisson", "bursty", "mixed"]
ARR_LBL = {"poisson": "Poisson", "bursty": "Bursty", "mixed": "Mixed"}
COLORS = {"rr": "#1f77b4", "bestfit": "#2ca02c", "drf": "#9467bd",
          "p2c": "#ff7f0e", "stps": "#d62728"}


def _load(path: Path):
    d = {}
    for r in csv.DictReader(path.open()):
        d[(r["scheduler"], r["arrival_mode"])] = r
    return d


def main() -> int:
    PIC.mkdir(parents=True, exist_ok=True)
    d16 = _load(ROOT / "data/q0/scale16_summary.csv")
    METRIC, CI = "avg_congestion_ratio_mean", "avg_congestion_ratio_ci95"

    # 16-card main experiment: one cell per arrival mode.
    cells = [("16 cards", a, d16) for a in ARRIVALS]

    fig, ax = plt.subplots(figsize=(6.2, 3.1))
    n_sched = len(SCHEDULERS)
    group_w = 0.82
    bar_w = group_w / n_sched
    xticks, xlabels = [], []

    for gi, (scale, arr, data) in enumerate(cells):
        base = min(float(data[(s, arr)][METRIC]) for s in BASELINES)
        x0 = gi
        for si, s in enumerate(SCHEDULERS):
            row = data[(s, arr)]
            val = float(row[METRIC]) / base
            ci = float(row[CI]) / base
            x = x0 - group_w / 2 + (si + 0.5) * bar_w
            ax.bar(x, val, bar_w * 0.95, color=COLORS[s],
                   edgecolor="black", linewidth=0.3,
                   yerr=ci, capsize=1.6, error_kw=dict(lw=0.7),
                   label=LABELS[s] if gi == 0 else None,
                   zorder=3 if s == "stps" else 2)
        xticks.append(x0)
        xlabels.append(ARR_LBL[arr])

    ax.axhline(1.0, color="black", ls="--", lw=0.9, zorder=1)
    ax.text(-0.42, 1.002, "best baseline = 1.0", ha="left", va="bottom", fontsize=7)
    ax.set_xticks(xticks)
    ax.set_xticklabels(xlabels, fontsize=8)
    ax.set_ylabel("congestion ratio\n(norm. to best baseline)", fontsize=9)
    ax.set_ylim(0.88, 1.10)
    ax.legend(ncol=5, fontsize=8, loc="upper center",
              bbox_to_anchor=(0.5, 1.18), framealpha=0.9)
    ax.grid(True, axis="y", alpha=0.3, zorder=0)
    fig.tight_layout()
    out = PIC / "fig_congestion_bars.pdf"
    fig.savefig(out, bbox_inches="tight")
    fig.savefig(out.with_suffix(".png"), dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"saved {out}")

    # sanity: is STPS below 1.0 in every cell?
    ok = all(float(data[("stps", arr)][METRIC]) < min(float(data[(s, arr)][METRIC]) for s in BASELINES)
             for _, arr, data in cells)
    print(f"STPS below best baseline in all 3 (16-card) cells: {ok}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
