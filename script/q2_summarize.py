"""Render Q2 result tables from main4 / main16 raw + summary CSVs.

Usage: q2_summarize.py <name>
e.g.   q2_summarize.py main4_bw9e5_d16
       q2_summarize.py main16_bw9e5_d16
"""
from __future__ import annotations

import argparse
import csv
import math
import numpy as np
from collections import defaultdict
from pathlib import Path
from statistics import mean, stdev
from typing import Dict, List, Sequence

ROOT = Path(__file__).resolve().parents[1]

PAIRS = [
    ("rr", "rr-phase", "RR"),
    ("bestfit", "bestfit-phase", "BestFit"),
    ("drf", "drf-phase", "DRF"),
    ("p2c", "p2c-phase", "P2C"),
    ("stps-spatial", "stps", "STPS-spatial"),
]
ARRIVALS = ["poisson", "bursty"]
SEEDS = [21, 42, 99, 123, 2024]


def _read_csv(path: Path) -> List[Dict[str, str]]:
    with path.open() as h:
        return list(csv.DictReader(h))


def _fnum(x: str) -> float:
    try:
        return float(x)
    except Exception:
        return float("nan")


def _ci95(vals: Sequence[float]) -> float:
    if len(vals) < 2:
        return 0.0
    return 1.96 * stdev(vals) / math.sqrt(len(vals))


def _f(x: float, p: int = 3) -> str:
    if x != x:  # nan
        return "—"
    if abs(x) >= 100:
        return f"{x:.{max(0, p - 2)}f}"
    return f"{x:.{p}f}"


def _pct(delta: float) -> str:
    if delta != delta:
        return "—"
    return f"{delta * 100:+.1f}%"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("name", help="output prefix e.g. main4_bw9e5_d16")
    parser.add_argument("--data-dir", default="data/q2")
    args = parser.parse_args()

    data_dir = ROOT / args.data_dir
    summary_path = data_dir / f"{args.name}_summary.csv"
    raw_path = data_dir / f"{args.name}_raw.csv"
    pc_path = data_dir / f"{args.name}_percard_summary.csv"

    summary = _read_csv(summary_path)
    raw = _read_csv(raw_path)
    pc = _read_csv(pc_path)

    cards = int(_fnum(raw[0]["cards"]))
    bw_max = _fnum(raw[0]["bw_max"])
    d_max = int(_fnum(raw[0]["d_max"]))
    tasks = int(_fnum(raw[0]["tasks"]))

    print(f"\n=== {args.name} ===")
    print(f"cards={cards}  tasks={tasks}  steps=512  BW_MAX={bw_max:g}  D_MAX={d_max}\n")

    # -------- Table Q2-2 style: cross-card balance + throughput + cold-start ----
    # index by (arrival, base_algo, phase_enabled)
    by_grp: Dict[tuple, Dict] = {}
    for row in summary:
        key = (row["arrival_mode"], row["base_algo"], row["phase_enabled"].lower() == "true")
        by_grp[key] = row

    cv_stats_by_grp: Dict[tuple, Dict[str, float]] = {}
    raw_cv_buckets: Dict[tuple, List[float]] = defaultdict(list)
    for row in raw:
        key = (row["arrival_mode"], row["base_algo"], row["phase_enabled"].lower() == "true")
        raw_cv_buckets[key].append(_fnum(row["card_cv"]))
    for key, vals in raw_cv_buckets.items():
        arr = np.asarray([v for v in vals if v == v], dtype=np.float64)
        if arr.size == 0:
            cv_stats_by_grp[key] = {"mean": float("nan"), "median": float("nan"), "p95": float("nan"), "std": float("nan")}
            continue
        cv_stats_by_grp[key] = {
            "mean": float(arr.mean()),
            "median": float(np.median(arr)),
            "p95": float(np.percentile(arr, 95)),
            "std": float(arr.std()),
        }

    print("\n--- Cross-card balance + throughput summary (mean over 5 seeds) ---")
    print(
        "arrival | algo | phase | CV mean | CV median | CV p95 | CV std | JFI | LIF | Max/Min | "
        "thr | thr_excl_cs | mean_cs | p99 | p99_excl_cs | avg_delay | avg_delay_excl_cs"
    )
    for arrival in ARRIVALS:
        for base, _, label in PAIRS:
            for phase in (False, True):
                row = by_grp.get((arrival, label, phase))
                if row is None:
                    continue
                cv_stats = cv_stats_by_grp.get((arrival, label, phase), {})
                cv_mean = cv_stats.get("mean", _fnum(row.get("card_cv_mean", "nan")))
                cv_median = cv_stats.get("median", float("nan"))
                cv_p95 = cv_stats.get("p95", float("nan"))
                cv_std = cv_stats.get("std", float("nan"))
                jfi = _fnum(row["card_jfi_mean"])
                lif = _fnum(row["card_lif_mean"])
                mmr = _fnum(row["max_min_ratio_mean"])
                thr = _fnum(row["throughput_mean"])
                thr_excl = _fnum(row["throughput_excl_cold_mean"])
                cs = _fnum(row["mean_cold_start_mean"])
                p99 = _fnum(row["p99_delay_mean"])
                p99_excl = _fnum(row["p99_delay_excl_cold_mean"])
                ad = _fnum(row["avg_delay_mean"])
                ad_excl = _fnum(row["avg_delay_excl_cold_mean"])
                print(
                    f"{arrival:7s} | {label:12s} | {'on ' if phase else 'off'} | "
                    f"{cv_mean:.4f} | {cv_median:.4f} | {cv_p95:.4f} | {cv_std:.4f} | "
                    f"{jfi:.4f} | {lif:.3f} | {mmr:5.2f} | "
                    f"{thr:.3f} | {thr_excl:.3f} | {cs:5.2f} | "
                    f"{p99:6.1f} | {p99_excl:6.1f} | {ad:5.2f} | {ad_excl:5.2f}"
                )

    # -------- Deltas (phase=on vs phase=off) --------
    print("\n--- Deltas phase=on vs off: % change (negative = improvement for ↓ metrics) ---")
    print(
        "arrival | algo | ΔCV | ΔJFI | ΔLIF | ΔMaxMin | Δthr | Δthr_excl_cs | "
        "ΔavgDelay | ΔavgDelay_excl_cs"
    )
    for arrival in ARRIVALS:
        for base, _, label in PAIRS:
            off = by_grp.get((arrival, label, False))
            on = by_grp.get((arrival, label, True))
            if not off or not on:
                continue

            def delta(k: str) -> float:
                a = _fnum(off[k])
                b = _fnum(on[k])
                if a == 0:
                    return float("nan")
                return (b - a) / a
            print(
                f"{arrival:7s} | {label:12s} | "
                f"{_pct(delta('card_cv_mean'))} | {_pct(delta('card_jfi_mean'))} | "
                f"{_pct(delta('card_lif_mean'))} | {_pct(delta('max_min_ratio_mean'))} | "
                f"{_pct(delta('throughput_mean'))} | {_pct(delta('throughput_excl_cold_mean'))} | "
                f"{_pct(delta('avg_delay_mean'))} | {_pct(delta('avg_delay_excl_cold_mean'))}"
            )

    # -------- Per-card time_lif (Q2-3 / Q2-4 style) --------
    pc_by = defaultdict(dict)  # (arrival, label, phase, kind) -> {card_id: row}
    for row in pc:
        key = (
            row["arrival_mode"],
            row["base_algo"],
            row["phase_enabled"].lower() == "true",
            row["kind"],
        )
        pc_by[key][int(_fnum(row["card_id"]))] = row

    print("\n--- Per-card time_lif (demand) ---")
    print("arrival | algo | phase | " + " | ".join(f"c{c}" for c in range(cards)) + " | mean")
    for arrival in ARRIVALS:
        for base, _, label in PAIRS:
            for phase in (False, True):
                cards_data = pc_by.get((arrival, label, phase, "demand"), {})
                if not cards_data:
                    continue
                lifs = [
                    _fnum(cards_data.get(c, {}).get("time_lif_mean", "nan"))
                    for c in range(cards)
                ]
                avg = mean([v for v in lifs if v == v]) if lifs else float("nan")
                print(
                    f"{arrival:7s} | {label:12s} | {'on ' if phase else 'off'} | "
                    + " | ".join(_f(v) for v in lifs)
                    + f" | {_f(avg)}"
                )

    # -------- Per-card throughput (Q2-5/Q2-6 style) ---------
    print("\n--- Per-card throughput (tasks/step, served) ---")
    print("arrival | algo | phase | " + " | ".join(f"c{c}" for c in range(cards)) + " | total")
    for arrival in ARRIVALS:
        for base, _, label in PAIRS:
            for phase in (False, True):
                cards_data = pc_by.get((arrival, label, phase, "served"), {})
                if not cards_data:
                    continue
                ths = [
                    _fnum(cards_data.get(c, {}).get("throughput_mean", "nan"))
                    for c in range(cards)
                ]
                tot = sum(v for v in ths if v == v)
                print(
                    f"{arrival:7s} | {label:12s} | {'on ' if phase else 'off'} | "
                    + " | ".join(_f(v) for v in ths)
                    + f" | {_f(tot)}"
                )

    # -------- Cold-start, offset, congestion, etc. (raw stats for context) -------
    print("\n--- Offset / congestion (mean over 5 seeds) ---")
    print(
        "arrival | algo | phase | mean_offset | p95_offset | "
        "avg_cong | mean_cong_wait | reject_bw"
    )
    for arrival in ARRIVALS:
        for base, _, label in PAIRS:
            for phase in (False, True):
                row = by_grp.get((arrival, label, phase))
                if row is None:
                    continue
                mo = _fnum(row["mean_start_offset_mean"])
                po = _fnum(row["p95_start_offset_mean"])
                ac = _fnum(row["avg_congestion_ratio_mean"])
                cw = _fnum(row["mean_congestion_wait_ticks_mean"])
                rj = _fnum(row["reject_rate_bw_mean"])
                print(
                    f"{arrival:7s} | {label:12s} | {'on ' if phase else 'off'} | "
                    f"{mo:5.2f} | {po:5.2f} | "
                    f"{ac:.3f} | {cw:6.2f} | {rj:.3f}"
                )

    # -------- CI95 for paired phase-on/off deltas, using raw rows ---
    # Index raw by (arrival, label, phase, seed)
    raw_by = {}
    for r in raw:
        k = (
            r["arrival_mode"],
            r["base_algo"],
            r["phase_enabled"].lower() == "true",
            int(_fnum(r["seed"])),
        )
        raw_by[k] = r

    print("\n--- Paired deltas with CI95 (mean ± CI over 5 seeds) ---")
    print("arrival | algo | ΔCV (mean ± CI95)")
    for arrival in ARRIVALS:
        for base, _, label in PAIRS:
            ds = []
            for s in SEEDS:
                off = raw_by.get((arrival, label, False, s))
                on = raw_by.get((arrival, label, True, s))
                if not off or not on:
                    continue
                a = _fnum(off["card_cv"])
                b = _fnum(on["card_cv"])
                if a == 0:
                    continue
                ds.append((b - a) / a)
            if not ds:
                continue
            m = mean(ds)
            ci = _ci95(ds)
            print(f"{arrival:7s} | {label:12s} | {m * 100:+.2f}% ± {ci * 100:.2f}%")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
