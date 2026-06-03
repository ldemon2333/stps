"""Aggregate the C-tier (BW_MAX=9e5, tasks=400) D_MAX sweep into markdown tables.

Reads data/q2/main4_bw9e5_d{2,4,8,16}_t400_summary.csv and emits, per arrival
mode, a table comparing phase=off baseline against phase=on at D_MAX in
{2,4,8,16}. Focus metrics: throughput, load balance (CV/JFI/LIF/Max-Min),
p99 delay, congestion (ratio / wait / reject).
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Dict, Tuple

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "q2"
D_LIST = [2, 4, 8, 16]
ARRIVALS = ["poisson", "bursty"]
ALGOS = ["RR", "BestFit", "DRF", "P2C", "STPS-spatial"]

# key -> column in summary csv (all suffixed _mean)
COLS = {
    "tput": "throughput_mean",
    "tput_xc": "throughput_excl_cold_mean",
    "cv": "card_cv_mean",
    "jfi": "card_jfi_mean",
    "lif": "card_lif_mean",
    "mmr": "max_min_ratio_mean",
    "p99": "p99_delay_mean",
    "cong": "avg_congestion_ratio_mean",
    "wait": "mean_congestion_wait_ticks_mean",
    "rej": "reject_rate_bw_mean",
    "off": "mean_start_offset_mean",
}


def load(d: int) -> Dict[Tuple[str, str, str], Dict[str, float]]:
    path = DATA / f"main4_bw9e5_d{d}_t400_summary.csv"
    out: Dict[Tuple[str, str, str], Dict[str, float]] = {}
    for r in csv.DictReader(path.open()):
        key = (r["arrival_mode"], r["base_algo"], r["phase_enabled"])
        out[key] = {k: float(r[c]) for k, c in COLS.items()}
    return out


def main() -> int:
    data = {d: load(d) for d in D_LIST}

    for arrival in ARRIVALS:
        print(f"\n### arrival = {arrival}\n")
        hdr = ("| Base Algo | D_MAX | tput ↑ | tput(xc) ↑ | CV ↓ | JFI ↑ | LIF ↓ | "
               "Max/Min ↓ | p99 ↓ | cong.ratio ↓ | cong.wait ↓ | reject_bw | mean offset |")
        sep = "|" + "---|" * 13
        print(hdr)
        print(sep)
        for algo in ALGOS:
            # phase=off baseline (shared; take from D=16 file)
            off = data[16][(arrival, algo, "False")]
            print(f"| {algo} | off | {off['tput']:.3f} | {off['tput_xc']:.3f} | "
                  f"{off['cv']:.4f} | {off['jfi']:.3f} | {off['lif']:.3f} | "
                  f"{off['mmr']:.2f} | {off['p99']:.1f} | {off['cong']:.4f} | "
                  f"{off['wait']:.2f} | {off['rej']:.3f} | {off['off']:.2f} |")
            for d in D_LIST:
                on = data[d][(arrival, algo, "True")]
                print(f"| {algo} | {d} | {on['tput']:.3f} | {on['tput_xc']:.3f} | "
                      f"{on['cv']:.4f} | {on['jfi']:.3f} | {on['lif']:.3f} | "
                      f"{on['mmr']:.2f} | {on['p99']:.1f} | {on['cong']:.4f} | "
                      f"{on['wait']:.2f} | {on['rej']:.3f} | {on['off']:.2f} |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
