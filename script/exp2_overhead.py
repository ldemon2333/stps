#!/usr/bin/env python3
"""EXP-2 (mechanism overhead): what does the scheduler itself cost?

Reviewers ask what the admission-time machinery costs. We report:

  (a) per-admission decision latency (microseconds), mean and p99, for every
      scheduler at 4 and 16 cards, by timing each `select_card_for_task`
      call in a real simulation (monkeypatched, zero engine change);
  (b) offline fingerprint artifact size (the .npz the scheduler consumes);
  (c) offline fingerprint extraction cost (one-time, off the admission path).

STPS's online cost is O(M) macro-dispatch + O(D_max*H) phase-shift; the
per-neuron centrality is precomputed offline and stored in the fingerprint, so
it is *not* paid per admission. This experiment confirms that empirically.

Output: data/overhead_summary.csv + printed table for the paper.
"""
from __future__ import annotations

import csv
import os
import sys
import time
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
import schedule  # noqa: F401  (populates registry)
from schedule.base import get_scheduler
from simulation.engine import run_simulation

SCHEDULERS = ["rr", "bestfit", "drf", "p2c", "stps-spatial", "stps"]
LABELS = {"rr": "RR", "bestfit": "BestFit", "drf": "DRF", "p2c": "P2C",
          "stps-spatial": "STPS-A (macro only)", "stps": "STPS (full)"}
SEED = 21
STEPS = 512
MIXED = ["synthetic_flat.npz", "synthetic_pulse_t8.npz", "synthetic_pulse_t16.npz",
         "synthetic_bursty.npz", "spikformer_cifar10.npz", "qkformer_cifar10.npz",
         "spikingresformer_ti_imagenet.npz"]

# Shared timing buffer, refreshed per run.
_TIMES: list = []


def _build_fp_dir(parent: Path) -> str:
    parent.mkdir(parents=True, exist_ok=True)
    for name in MIXED:
        save_fingerprint(parent / name, load_fingerprint(ROOT / "npz" / name))
    return str(parent)


def _timed_latencies(scheduler: str, cards: int, tasks: int, fp_dir: str):
    """Run one sim with select_card_for_task instrumented; return latency array (us)."""
    cls = get_scheduler(scheduler)
    orig = cls.select_card_for_task
    times: list = []

    def wrapper(self, task):
        t0 = time.perf_counter()
        r = orig(self, task)
        times.append((time.perf_counter() - t0) * 1e6)  # microseconds
        return r

    cls.select_card_for_task = wrapper
    try:
        run_simulation(
            scheduler=scheduler, cards=cards, tasks=tasks, steps=STEPS, seed=SEED,
            arrival_mode="bursty", fingerprint_dir=fp_dir, bw_max=9e5, bw_cap=9e5,
            d_max=2, horizon=64, centrality_split_threshold=0.2,
            log_dir="log", data_dir=f"data/_exp2/{scheduler}_{cards}",
        )
    finally:
        cls.select_card_for_task = orig
    return np.asarray(times, dtype=np.float64)


def main() -> int:
    import tempfile
    rows = []
    with tempfile.TemporaryDirectory(prefix="exp2_fp_") as tmp:
        fp_dir = _build_fp_dir(Path(tmp) / "mixed")
        for cards, tasks in [(4, 800), (16, 3200)]:
            for sch in SCHEDULERS:
                lat = _timed_latencies(sch, cards, tasks, fp_dir)
                lat = lat[lat > 0]
                if lat.size == 0:
                    continue
                rows.append({
                    "scheduler": sch, "cards": cards,
                    "n_calls": int(lat.size),
                    "mean_us": float(lat.mean()),
                    "median_us": float(np.median(lat)),
                    "p99_us": float(np.percentile(lat, 99)),
                    "max_us": float(lat.max()),
                })
                print(f"  {LABELS[sch]:22s} {cards:2d} cards: "
                      f"mean={lat.mean():8.2f}us p99={np.percentile(lat,99):9.2f}us",
                      flush=True)

    # Fingerprint artifact sizes.
    print("\n=== fingerprint artifact sizes ===")
    fp_rows = []
    for name in MIXED:
        kb = (ROOT / "npz" / name).stat().st_size / 1024.0
        fp = load_fingerprint(ROOT / "npz" / name)
        V = int(getattr(fp, "neuron_count", 0))
        fp_rows.append({"fingerprint": name, "size_kb": round(kb, 1), "neurons": V})
        print(f"  {name:34s} {kb:6.1f} KB  (neurons={V:,})")

    # Offline precompute cost: the Stage-3 hotspot split is a pure function of
    # the fingerprint (computed once per fingerprint, off the admission path).
    # Time it on each real per-neuron centrality array.
    from schedule.hotspot_split import split_population
    print("\n=== offline Stage-3 split cost (one-time per fingerprint) ===")
    ext_rows = []
    for name in MIXED:
        fp = load_fingerprint(ROOT / "npz" / name)
        c = fp.max_centrality
        t0 = time.perf_counter()
        split_population(c, 0.2)
        dt = time.perf_counter() - t0
        ext_rows.append({"fingerprint": name, "neurons": int(c.size),
                         "split_ms": round(dt * 1e3, 2)})
        print(f"  {name:34s} V'={c.size:>10,}  {dt*1e3:8.1f} ms (one-time)")

    out = ROOT / "data" / "overhead_summary.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="") as h:
        w = csv.DictWriter(h, fieldnames=["scheduler", "cards", "n_calls",
                                          "mean_us", "median_us", "p99_us", "max_us"])
        w.writeheader()
        w.writerows(rows)
    with (ROOT / "data" / "overhead_fingerprints.csv").open("w", newline="") as h:
        w = csv.DictWriter(h, fieldnames=["fingerprint", "size_kb", "neurons"])
        w.writeheader()
        w.writerows(fp_rows)
    with (ROOT / "data" / "overhead_split.csv").open("w", newline="") as h:
        w = csv.DictWriter(h, fieldnames=["fingerprint", "neurons", "split_ms"])
        w.writeheader()
        w.writerows(ext_rows)
    print(f"\nsaved {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
