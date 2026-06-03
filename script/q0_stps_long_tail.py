"""Q0 STPS-only long-tail figures.

Produces 4 single-panel long-tail delay histograms (PDF + PNG):

    figures/q0/stps_long_tail_4card_poisson.{pdf,png}
    figures/q0/stps_long_tail_4card_bursty.{pdf,png}
    figures/q0/stps_long_tail_16card_poisson.{pdf,png}
    figures/q0/stps_long_tail_16card_bursty.{pdf,png}

Axes / style match ``plot_long_tail`` in script/q0_run.py:
    - x: Task delay (ticks), linear, shared bin edges 0..d_max+1
    - y: # tasks (log), dashed line at pooled P99, title "(P99=…, tail=…/…, …%)"

Data sources:
    - 16-card delays are loaded from the existing dump
      ``data/q0/scale16_task_delays.csv`` (filtered to scheduler == "stps").
    - 4-card delays are produced on the fly by re-running STPS for
      ``arrival ∈ {poisson, bursty}`` × 5 seeds, with the same knobs as
      ``q0_run.run_main`` (cards=4, tasks=800, bw_cap=9e5, d_max=2,
      horizon=64, mixed fingerprint set).

Usage::

    /root/miniconda3/envs/snn/bin/python script/q0_stps_long_tail.py
"""
from __future__ import annotations

import csv
import logging
import math
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Sequence

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Silence the framework loggers exactly like q0_run.py does.
_root = logging.getLogger()
_root.setLevel(logging.ERROR)
if not _root.handlers:
    _root.addHandler(logging.NullHandler())
for name in ("simulation", "schedule", "util", "fingerprint", "simulation.engine"):
    logging.getLogger(name).setLevel(logging.ERROR)

# Reuse q0_run helpers so 4-card delays are produced with the exact same
# knobs / fingerprint construction as the published scale16 dump.
from script.q0_run import (  # noqa: E402
    SEEDS,
    DEFAULT_TASKS,
    _build_mixed_fp_dir,
    _run_one,
)

STPS_COLOR = "#d62728"  # same red as the original palette in q0_run.plot_long_tail
STPS_LABEL = "STPS (full)"
OUT_DIR = ROOT / "figures" / "q0"
SCALE16_DELAY_CSV = ROOT / "data" / "q0" / "scale16_task_delays.csv"


# --------------------------------------------------------------------------- #
# Data loading
# --------------------------------------------------------------------------- #
def _load_scale16_stps_delays() -> Dict[str, np.ndarray]:
    """Return {arrival_mode: int64 delays} for scheduler == 'stps' from CSV."""
    if not SCALE16_DELAY_CSV.exists():
        raise FileNotFoundError(
            f"Missing {SCALE16_DELAY_CSV}; run `python script/q0_run.py scale16` first."
        )
    pooled: Dict[str, List[int]] = {}
    with SCALE16_DELAY_CSV.open() as f:
        reader = csv.DictReader(f)
        for r in reader:
            if r["scheduler"] != "stps":
                continue
            pooled.setdefault(r["arrival_mode"], []).append(int(r["delay"]))
    return {k: np.asarray(v, dtype=np.int64) for k, v in pooled.items()}


def _run_4card_stps_delays(fp_dir: str) -> Dict[str, np.ndarray]:
    """Re-run STPS at 4-card x {poisson, bursty} x 5 seeds and pool delays."""
    pooled: Dict[str, List[int]] = {"poisson": [], "bursty": []}
    for arrival in ("poisson", "bursty"):
        for seed in SEEDS:
            print(
                f"[stps-long-tail] cards=4 arrival={arrival:7s} seed={seed} ...",
                flush=True,
            )
            _, delay_dict = _run_one(
                "stps", seed, DEFAULT_TASKS, arrival, fp_dir, cards=4,
            )
            pooled[arrival].extend(delay_dict["delay"].tolist())
    return {k: np.asarray(v, dtype=np.int64) for k, v in pooled.items()}


# --------------------------------------------------------------------------- #
# Plotting
# --------------------------------------------------------------------------- #
def _plot_one(delays: np.ndarray, out_path: Path, *, cards: int, arrival: str) -> None:
    """Single-panel STPS long-tail histogram (matches q0_run.plot_long_tail style)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    if delays.size == 0:
        print(f"[stps-long-tail] skip {out_path}: empty delay vector")
        return

    d_max = int(delays.max())
    # Same bin policy as q0_run.plot_long_tail.
    n_bins = min(120, max(20, d_max // 2))
    bins = np.linspace(0, max(d_max, 1) + 1, n_bins + 1)

    p99 = float(np.percentile(delays, 99))
    tail = int((delays > p99).sum())
    n = int(delays.size)

    fig, ax = plt.subplots(figsize=(6.0, 4.0), dpi=200)
    ax.hist(
        delays, bins=bins,
        color=STPS_COLOR, alpha=0.85, edgecolor="none", log=True,
    )
    ax.axvline(p99, color="black", linestyle="--", linewidth=1.0, alpha=0.7)
    ax.set_ylim(0.5, max(2.0, float(n)))
    ax.set_xlabel("Task delay (ticks)", fontsize=10)
    ax.set_ylabel("# tasks (log)", fontsize=10)
    ax.set_title(
        f"{STPS_LABEL} — {cards} cards, arrival={arrival} "
        f"(P99={p99:.0f}, tail={tail}/{n}, {tail / n * 100:.2f}%)",
        fontsize=10,
    )
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    fig.suptitle(
        f"Long-tail delay distribution — STPS, {cards} cards, arrival={arrival}, "
        f"{len(SEEDS)} seeds pooled",
        fontsize=11,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, bbox_inches="tight")
    fig.savefig(out_path.with_suffix(".png"), bbox_inches="tight")
    plt.close(fig)
    print(f"[stps-long-tail] wrote {out_path}")


def _emit(delays_by_arrival: Dict[str, np.ndarray], cards: int) -> None:
    for arrival in ("poisson", "bursty"):
        d = delays_by_arrival.get(arrival)
        if d is None:
            print(f"[stps-long-tail] missing arrival={arrival} for cards={cards}; skip")
            continue
        out = OUT_DIR / f"stps_long_tail_{cards}card_{arrival}.pdf"
        _plot_one(d, out, cards=cards, arrival=arrival)


def main() -> int:
    # 16-card: reuse pre-computed delays from disk.
    print("[stps-long-tail] loading 16-card delays from "
          f"{SCALE16_DELAY_CSV.relative_to(ROOT)}")
    delays16 = _load_scale16_stps_delays()
    _emit(delays16, cards=16)

    # 4-card: produce fresh by re-running STPS on the mixed fingerprint set.
    with tempfile.TemporaryDirectory(prefix="q0_fp_stps_lt_") as tmp:
        fp_dir = _build_mixed_fp_dir(Path(tmp) / "mixed")
        delays4 = _run_4card_stps_delays(fp_dir)
    _emit(delays4, cards=4)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
