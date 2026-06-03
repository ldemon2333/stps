"""One-off d_max sweep — find smallest d_max where STPS retention ≥ baseline retention."""
from __future__ import annotations
import csv
import logging
import sys
import tempfile
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fingerprint import make_synthetic_fingerprint, save_fingerprint
from simulation.engine import run_simulation

logging.getLogger().setLevel(logging.ERROR)
for n in ("simulation", "schedule", "util", "fingerprint", "simulation.engine"):
    logging.getLogger(n).setLevel(logging.ERROR)

CARDS = 4
TASKS = 800
STEPS = 512
BW_CAP = 900_000.0
SEEDS = [11, 21, 42]
ARRIVALS = ["bursty", "mixed"]
D_MAX_VALUES = [0, 1, 2, 4, 8, 16]
SCHEDS = ["rr", "bestfit", "stps"]

SPECS = [
    ("synthetic_flat.npz", 4, 1.05, 1, 11),
    ("synthetic_pulse_t8.npz", 8, 1.8, 1, 12),
    ("synthetic_pulse_t16.npz", 16, 2.5, 1, 13),
    ("synthetic_bursty.npz", 4, 3.8, 1, 14),
]


def build_fp(parent: Path) -> str:
    parent.mkdir(parents=True, exist_ok=True)
    for name, T, beta, K, seed in SPECS:
        fp = make_synthetic_fingerprint(
            beta_target=beta, K=K, T=T, V=16,
            neuron_count=2_000_000, state_size_mb=32.0,
            complexity_ratio=1.0, e_mean=200000.0, seed=seed,
            meta={"source": "d-max-sweep", "beta_target": str(beta)},
        )
        save_fingerprint(parent / name, fp)
    return str(parent)


def run(sched, seed, bw_cap, arrival, fp_dir, d_max):
    m = run_simulation(
        scheduler=sched, cards=CARDS, tasks=TASKS, steps=STEPS, seed=seed,
        arrival_mode=arrival, fingerprint_dir=fp_dir,
        bw_max=bw_cap if bw_cap is not None else 1e9,
        bw_cap=bw_cap, d_max=d_max, horizon=64,
        centrality_split_threshold=0.2,
        log_dir="log", data_dir="data/q_traffic/_dmax_raw",
    )
    return (m.throughput, m.completion_rate, m.p99_delay,
            m.avg_congestion_ratio, m.mean_congestion_wait_ticks)


def main():
    out = Path("data/q_traffic/dmax_sweep.csv")
    rows = []
    with tempfile.TemporaryDirectory(prefix="dmax_") as tmp:
        fp_dir = build_fp(Path(tmp) / "synth")
        # Baselines run once (d_max irrelevant) per arrival.
        baseline = {}
        for arr in ARRIVALS:
            for sched in ["rr", "bestfit"]:
                for regime, cap in [("uncapped", None), ("capped", BW_CAP)]:
                    thr_vals, cr_vals, p99_vals = [], [], []
                    for s in SEEDS:
                        thr, cr, p99, _, _ = run(sched, s, cap, arr, fp_dir, 16)
                        thr_vals.append(thr); cr_vals.append(cr); p99_vals.append(p99)
                    baseline[(sched, arr, regime)] = (mean(thr_vals), mean(cr_vals), mean(p99_vals))
                    print(f"  baseline {sched:8s} {arr:8s} {regime:8s} "
                          f"thr={mean(thr_vals):.4f} cr={mean(cr_vals):.4f}")
        # STPS sweep over d_max.
        stps_data = {}
        for arr in ARRIVALS:
            for d in D_MAX_VALUES:
                for regime, cap in [("uncapped", None), ("capped", BW_CAP)]:
                    thr_vals, cr_vals, p99_vals, cr_ratio, cw = [], [], [], [], []
                    for s in SEEDS:
                        thr, cr, p99, ccr, cwait = run("stps", s, cap, arr, fp_dir, d)
                        thr_vals.append(thr); cr_vals.append(cr); p99_vals.append(p99)
                        cr_ratio.append(ccr); cw.append(cwait)
                    stps_data[(arr, d, regime)] = (
                        mean(thr_vals), mean(cr_vals), mean(p99_vals),
                        mean(cr_ratio), mean(cw),
                    )
                    print(f"  stps d_max={d:2d} {arr:8s} {regime:8s} "
                          f"thr={mean(thr_vals):.4f} cr={mean(cr_vals):.4f} "
                          f"p99={mean(p99_vals):.2f} cong={mean(cr_ratio):.4f} cw={mean(cw):.2f}")

        # Print summary table.
        for arr in ARRIVALS:
            rr_u, rr_uc, _ = baseline[("rr", arr, "uncapped")]
            rr_c, rr_cc, _ = baseline[("rr", arr, "capped")]
            bf_u, _, _ = baseline[("bestfit", arr, "uncapped")]
            bf_c, _, _ = baseline[("bestfit", arr, "capped")]
            rr_drop = (rr_u - rr_c) / rr_u
            bf_drop = (bf_u - bf_c) / bf_u
            print(f"\n=== arrival={arr}  rr drop={rr_drop:.3%}  bestfit drop={bf_drop:.3%} ===")
            print(f"{'d_max':>5} {'thr_unc':>9} {'thr_cap':>9} {'drop':>8} {'cr_cap':>7} "
                  f"{'p99_cap':>8} {'cong_cap':>9} {'cw_cap':>7}  beats?")
            for d in D_MAX_VALUES:
                tu, cru, p99u, _, _ = stps_data[(arr, d, "uncapped")]
                tc, crc, p99c, congc, cwc = stps_data[(arr, d, "capped")]
                drop = (tu - tc) / tu if tu > 0 else 0
                # "Beats baseline" requires: completion_rate==1 AND drop < min(rr_drop, bf_drop).
                ok = (crc >= 0.999) and (drop < min(rr_drop, bf_drop))
                rows.append({
                    "arrival": arr, "d_max": d,
                    "thr_uncapped": tu, "thr_capped": tc, "stps_drop": drop,
                    "completion_rate_capped": crc, "p99_capped": p99c,
                    "cong_ratio_capped": congc, "cong_wait_capped": cwc,
                    "rr_drop": rr_drop, "bestfit_drop": bf_drop,
                    "beats_baselines": ok,
                })
                tag = " ✓" if ok else ""
                print(f"{d:>5} {tu:>9.4f} {tc:>9.4f} {drop:>7.2%} {crc:>7.3f} "
                      f"{p99c:>8.2f} {congc:>9.4f} {cwc:>7.2f}{tag}")
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
