"""Traffic / bandwidth-contention experiment runner (docs/traffic_optim.md).

Subcommands:
    calib       Phase A — sweep BW_CAP with RR (+ baseline check) and pick BW_CAP_4*.
    main        Phase B — full algorithm re-compare under BW_CAP* (placeholder).
    sensitivity Phase B — BW_CAP sensitivity sweep (placeholder).

This module re-uses q0_run.py's helper conventions (synthetic fingerprint
directory, _ci95, _aggregate, _write_csv). The Phase B subcommands are wired
but the user requested only Phase A be executed in this round, so they exist
for follow-up runs and are documented in docs/traffic_optim.md §3.
"""
from __future__ import annotations

import argparse
import csv
import logging
import math
import sys
import tempfile
from pathlib import Path
from statistics import mean, stdev
from typing import Dict, List, Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fingerprint import make_synthetic_fingerprint, save_fingerprint, load_fingerprint
from simulation.engine import run_simulation

# Silence engine INFO logs as in q0_run.py.
_root = logging.getLogger()
_root.setLevel(logging.ERROR)
if not _root.handlers:
    _root.addHandler(logging.NullHandler())
for n in ("simulation", "schedule", "util", "fingerprint", "simulation.engine"):
    logging.getLogger(n).setLevel(logging.ERROR)

CARDS_4 = 4
TASKS_4 = 800
STEPS = 512
ARRIVAL = "bursty"
SEEDS_REF = [21, 42]  # RR sweep seeds (Phase A §2.3.1 step 3)
SEEDS_MAIN = [11, 21, 42, 73, 101]  # Phase B main4 seeds (5 per docs §3.1)
SEEDS_SENS = [21, 42, 73]  # Phase B sensitivity seeds (3 per docs §3.1)
SCHEDULERS_CHECK = ["bestfit", "drf", "p2c"]  # baseline verification at BW_CAP*
SCHEDULERS_MAIN = ["rr", "bestfit", "drf", "p2c", "stps", "stps-spatial", "stps-temporal"]
ARRIVAL_MODES_4 = ["poisson", "bursty", "mixed"]
BW_CAP_4_STAR = 900_000.0  # Phase A result (docs/traffic_calib.md)
SENS_MULTIPLIERS = [0.5, 1.0, 2.0]

SYNTHETIC_SPECS = [
    ("synthetic_flat.npz", 4, 1.05, 1, 11),
    ("synthetic_pulse_t8.npz", 8, 1.8, 1, 12),
    ("synthetic_pulse_t16.npz", 16, 2.5, 1, 13),
    ("synthetic_bursty.npz", 4, 3.8, 1, 14),
]

METRIC_KEYS = [
    "throughput", "p99_delay", "avg_delay", "completion_rate",
    "avg_congestion_ratio", "congested_card_tick_frac",
    "peak_backlog", "avg_utilization", "avg_demand_cv",
    "mean_congestion_wait_ticks", "p95_congestion_wait_ticks",
    "congestion_timeouts", "card_cv", "card_jfi", "card_lif", "max_min_ratio",
]


def _build_synthetic_fp_dir(parent: Path) -> str:
    parent.mkdir(parents=True, exist_ok=True)
    for name, T, beta, K, seed in SYNTHETIC_SPECS:
        fp = make_synthetic_fingerprint(
            beta_target=beta, K=K, T=T, V=16,
            neuron_count=2_000_000, state_size_mb=32.0,
            complexity_ratio=1.0, e_mean=200000.0, seed=seed,
            meta={"source": "q-traffic-synthetic", "beta_target": str(beta)},
        )
        save_fingerprint(parent / name, fp)
    return str(parent)


def _ci95(vals: Sequence[float]) -> float:
    n = len(vals)
    if n < 2:
        return 0.0
    return 1.96 * stdev(vals) / math.sqrt(n)


def _write_csv(path: Path, rows: List[Dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        return
    fieldnames: List[str] = []
    seen = set()
    for r in rows:
        for k in r:
            if k not in seen:
                seen.add(k)
                fieldnames.append(k)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)


def _aggregate(rows: List[Dict], group_keys: Sequence[str]) -> List[Dict]:
    buckets: Dict[tuple, List[Dict]] = {}
    for row in rows:
        key = tuple(row[k] for k in group_keys)
        buckets.setdefault(key, []).append(row)
    out = []
    for key, items in buckets.items():
        agg = {k: v for k, v in zip(group_keys, key)}
        agg["n"] = len(items)
        for m in METRIC_KEYS:
            vals = [r[m] for r in items if r.get(m) is not None]
            if not vals:
                agg[f"{m}_mean"] = 0.0
                agg[f"{m}_ci95"] = 0.0
                continue
            agg[f"{m}_mean"] = mean(vals)
            agg[f"{m}_ci95"] = _ci95(vals)
        out.append(agg)
    return out


def _run_one(scheduler: str, seed: int, bw_cap: Optional[float],
             fp_dir: str, cards: int, tasks: int) -> Dict:
    bw_max = bw_cap if bw_cap is not None else 1e9
    metrics = run_simulation(
        scheduler=scheduler,
        cards=cards,
        tasks=tasks,
        steps=STEPS,
        seed=seed,
        arrival_mode=ARRIVAL,
        fingerprint_dir=fp_dir,
        bw_max=bw_max,
        bw_cap=bw_cap,
        d_max=2,
        horizon=64,
        centrality_split_threshold=0.2,
        log_dir="log",
        data_dir="data/q_traffic/_raw",
    )
    return {
        "scheduler": scheduler,
        "seed": seed,
        "cards": cards,
        "tasks": tasks,
        "bw_cap": "inf" if bw_cap is None else bw_cap,
        "throughput": metrics.throughput,
        "p99_delay": metrics.p99_delay,
        "avg_delay": metrics.avg_delay,
        "completion_rate": metrics.completion_rate,
        "avg_congestion_ratio": metrics.avg_congestion_ratio,
        "congested_card_tick_frac": metrics.congested_card_tick_frac,
        "peak_backlog": metrics.peak_backlog,
        "avg_utilization": metrics.avg_utilization,
        "avg_demand_cv": metrics.avg_demand_cv,
        "mean_congestion_wait_ticks": metrics.mean_congestion_wait_ticks,
        "p95_congestion_wait_ticks": metrics.p95_congestion_wait_ticks,
        "congestion_timeouts": metrics.congestion_timeouts,
        "card_cv": metrics.avg_card_cv,
        "card_jfi": metrics.avg_card_jfi,
        "card_lif": metrics.avg_card_lif,
        "max_min_ratio": metrics.avg_max_min_ratio,
    }


def _demand_percentiles(fp_dir: str, cards: int, tasks: int, seed: int) -> Dict[str, float]:
    """Single-seed RR run with bw_cap=None; pull demand percentiles from the load CSV."""
    metrics = run_simulation(
        scheduler="rr",
        cards=cards,
        tasks=tasks,
        steps=STEPS,
        seed=seed,
        arrival_mode=ARRIVAL,
        fingerprint_dir=fp_dir,
        bw_max=1e9,
        bw_cap=None,
        d_max=2,
        horizon=64,
        centrality_split_threshold=0.2,
        log_dir="log",
        data_dir="data/q_traffic/_raw",
    )
    demands: List[float] = []
    # Skip first/last 64 ticks (steady-state window matches util/metrics.py).
    n = len(metrics.load_snapshots)
    lo, hi = (64, n - 64) if n > 128 else (0, n)
    for s in metrics.load_snapshots[lo:hi]:
        for v in s.card_demand.values():
            demands.append(float(v))
    arr = sorted(demands)
    def pct(p: float) -> float:
        if not arr:
            return 0.0
        idx = min(len(arr) - 1, int(round(p * (len(arr) - 1))))
        return float(arr[idx])
    return {
        "p50": pct(0.50), "p75": pct(0.75), "p90": pct(0.90),
        "p95": pct(0.95), "p99": pct(0.99), "max": max(arr) if arr else 0.0,
        "uncapped_throughput": metrics.throughput,
        "uncapped_p99_delay": metrics.p99_delay,
    }


def run_calib(out_dir: Path, fp_dir: str, cards: int = CARDS_4, tasks: int = TASKS_4) -> Path:
    """Phase A — sweep BW_CAP and write calibration table."""
    print(f"[traffic.calib] cards={cards} tasks={tasks} steps={STEPS} arrival={ARRIVAL}")
    # 1. Reference run: extract demand percentiles.
    print(f"  - reference RR run (bw_cap=inf, seed={SEEDS_REF[0]}) for demand percentiles ...",
          flush=True)
    pcts = _demand_percentiles(fp_dir, cards, tasks, SEEDS_REF[0])
    print(f"    demand: p50={pcts['p50']:.2e} p75={pcts['p75']:.2e} p90={pcts['p90']:.2e} "
          f"p95={pcts['p95']:.2e} p99={pcts['p99']:.2e} max={pcts['max']:.2e}")

    # 2. Build sweep candidates from percentiles + manual fallbacks.
    candidates: List[Optional[float]] = [None]  # ∞
    for k in ["p99", "p95", "p90", "p75", "p50"]:
        if pcts[k] > 0:
            candidates.append(pcts[k])
    # Manual fallbacks (kept rounded for table readability).
    for v in [5e6, 2e6, 1e6, 5e5, 2e5, 1e5]:
        candidates.append(float(v))
    # Deduplicate while preserving order.
    seen: set = set()
    dedup: List[Optional[float]] = []
    for c in candidates:
        key = "inf" if c is None else round(c, 2)
        if key in seen:
            continue
        seen.add(key)
        dedup.append(c)
    candidates = dedup

    rows: List[Dict] = []
    # 3. Sweep RR over candidates × 2 seeds.
    for cap in candidates:
        for seed in SEEDS_REF:
            label = "inf" if cap is None else f"{cap:.2e}"
            print(f"  - rr seed={seed} bw_cap={label} ...", flush=True)
            rows.append(_run_one("rr", seed, cap, fp_dir, cards, tasks))

    # 4. Pick BW_CAP*.
    rr_agg = _aggregate([r for r in rows if r["scheduler"] == "rr"], ["bw_cap"])
    # Find inf throughput baseline.
    inf_row = next((r for r in rr_agg if r["bw_cap"] == "inf"), None)
    inf_thr = inf_row["throughput_mean"] if inf_row else 0.0
    chosen_cap: Optional[float] = None
    # Sort numeric caps in descending order — pick first matching candidate.
    numeric = [r for r in rr_agg if r["bw_cap"] != "inf"]
    numeric.sort(key=lambda r: float(r["bw_cap"]), reverse=True)
    for r in numeric:
        thr_drop = (inf_thr - r["throughput_mean"]) / inf_thr if inf_thr > 0 else 0.0
        ratio = r["avg_congestion_ratio_mean"]
        frac = r["congested_card_tick_frac_mean"]
        timeouts = r["congestion_timeouts_mean"]
        if thr_drop >= 0.10 and 0.1 <= ratio <= 0.4 and 0.2 <= frac <= 0.6 and timeouts == 0:
            chosen_cap = float(r["bw_cap"])
            break
    # Fallback: if strict band fails, pick the highest cap with ≥5% drop and ratio≥0.05.
    if chosen_cap is None:
        for r in numeric:
            thr_drop = (inf_thr - r["throughput_mean"]) / inf_thr if inf_thr > 0 else 0.0
            if thr_drop >= 0.05 and r["avg_congestion_ratio_mean"] >= 0.05:
                chosen_cap = float(r["bw_cap"])
                break

    # 5. Baseline cross-check at chosen cap.
    if chosen_cap is not None:
        for sched in SCHEDULERS_CHECK:
            for seed in SEEDS_REF:
                print(f"  - {sched:8s} seed={seed} bw_cap={chosen_cap:.2e} (verify) ...",
                      flush=True)
                rows.append(_run_one(sched, seed, chosen_cap, fp_dir, cards, tasks))

    raw_path = out_dir / "calib_raw.csv"
    _write_csv(raw_path, rows)
    summary = _aggregate(rows, ["scheduler", "bw_cap"])
    _write_csv(out_dir / "calib_summary.csv", summary)

    # Write a small selection-summary file the docs writer can consume.
    sel_path = out_dir / "calib_selection.csv"
    with sel_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "key", "value",
        ])
        w.writerow(["cards", cards])
        w.writerow(["tasks", tasks])
        w.writerow(["steps", STEPS])
        w.writerow(["arrival", ARRIVAL])
        for k, v in pcts.items():
            w.writerow([f"demand_{k}", v])
        w.writerow(["chosen_bw_cap", chosen_cap if chosen_cap is not None else ""])

    print(f"[traffic.calib] wrote {raw_path}")
    print(f"[traffic.calib] chosen BW_CAP_4* = {chosen_cap}")
    return sel_path


def _uncapped_throughput(fp_dir: str, cards: int, tasks: int,
                         arrival: str, scheduler: str, seeds: Sequence[int]) -> float:
    """Mean throughput at bw_cap=None — used for 'throughput retention rate'."""
    vals = []
    for s in seeds:
        row = _run_one_arrival(scheduler, s, None, fp_dir, cards, tasks, arrival)
        vals.append(row["throughput"])
    return mean(vals) if vals else 0.0


def _run_one_arrival(scheduler: str, seed: int, bw_cap: Optional[float],
                     fp_dir: str, cards: int, tasks: int, arrival: str) -> Dict:
    bw_max = bw_cap if bw_cap is not None else 1e9
    metrics = run_simulation(
        scheduler=scheduler,
        cards=cards,
        tasks=tasks,
        steps=STEPS,
        seed=seed,
        arrival_mode=arrival,
        fingerprint_dir=fp_dir,
        bw_max=bw_max,
        bw_cap=bw_cap,
        d_max=2,
        horizon=64,
        centrality_split_threshold=0.2,
        log_dir="log",
        data_dir="data/q_traffic/_raw",
    )
    row = {
        "scheduler": scheduler,
        "seed": seed,
        "arrival": arrival,
        "cards": cards,
        "tasks": tasks,
        "bw_cap": "inf" if bw_cap is None else bw_cap,
        "throughput": metrics.throughput,
        "p99_delay": metrics.p99_delay,
        "avg_delay": metrics.avg_delay,
        "completion_rate": metrics.completion_rate,
        "avg_congestion_ratio": metrics.avg_congestion_ratio,
        "congested_card_tick_frac": metrics.congested_card_tick_frac,
        "peak_backlog": metrics.peak_backlog,
        "avg_utilization": metrics.avg_utilization,
        "avg_demand_cv": metrics.avg_demand_cv,
        "mean_congestion_wait_ticks": metrics.mean_congestion_wait_ticks,
        "p95_congestion_wait_ticks": metrics.p95_congestion_wait_ticks,
        "congestion_timeouts": metrics.congestion_timeouts,
        "card_cv": metrics.avg_card_cv,
        "card_jfi": metrics.avg_card_jfi,
        "card_lif": metrics.avg_card_lif,
        "max_min_ratio": metrics.avg_max_min_ratio,
    }
    return row


def run_main(out_dir: Path, fp_dir: str, bw_cap: float,
             cards: int = CARDS_4, tasks: int = TASKS_4) -> None:
    """Phase B §3.1 — 4-card algorithm re-compare under BW_CAP_4* (bursty + arrival sweep)."""
    print(f"[traffic.main] cards={cards} tasks={tasks} steps={STEPS} bw_cap={bw_cap:.2e}")
    rows: List[Dict] = []
    retention_rows: List[Dict] = []
    for arrival in ARRIVAL_MODES_4:
        # Uncapped reference per (scheduler, arrival) for retention rate.
        for sched in SCHEDULERS_MAIN:
            thr_inf_vals = []
            for s in SEEDS_MAIN:
                print(f"  - {sched:14s} seed={s} arrival={arrival} bw_cap=inf (ref) ...", flush=True)
                row_inf = _run_one_arrival(sched, s, None, fp_dir, cards, tasks, arrival)
                thr_inf_vals.append(row_inf["throughput"])
                row_inf["bw_regime"] = "uncapped"
                rows.append(row_inf)
            thr_inf_mean = mean(thr_inf_vals)
            thr_cap_vals = []
            for s in SEEDS_MAIN:
                print(f"  - {sched:14s} seed={s} arrival={arrival} bw_cap={bw_cap:.2e} ...", flush=True)
                row_cap = _run_one_arrival(sched, s, bw_cap, fp_dir, cards, tasks, arrival)
                thr_cap_vals.append(row_cap["throughput"])
                row_cap["bw_regime"] = "capped"
                rows.append(row_cap)
            thr_cap_mean = mean(thr_cap_vals)
            retention_rows.append({
                "scheduler": sched,
                "arrival": arrival,
                "bw_cap": bw_cap,
                "throughput_uncapped_mean": thr_inf_mean,
                "throughput_capped_mean": thr_cap_mean,
                "throughput_retention": thr_cap_mean / thr_inf_mean if thr_inf_mean > 0 else 0.0,
                "throughput_drop": (thr_inf_mean - thr_cap_mean) / thr_inf_mean if thr_inf_mean > 0 else 0.0,
            })

    raw_path = out_dir / "main4_raw.csv"
    _write_csv(raw_path, rows)
    summary = _aggregate(rows, ["scheduler", "arrival", "bw_regime"])
    _write_csv(out_dir / "main4_summary.csv", summary)
    _write_csv(out_dir / "main4_retention.csv", retention_rows)
    print(f"[traffic.main] wrote {raw_path}")


def run_sensitivity(out_dir: Path, fp_dir: str, bw_cap_star: float,
                    cards: int = CARDS_4, tasks: int = TASKS_4) -> None:
    """Phase B §3.1 — BW_CAP sensitivity sweep (4-card bursty, {0.5, 1.0, 2.0} × BW_CAP*)."""
    print(f"[traffic.sens] cards={cards} bw_cap_star={bw_cap_star:.2e} "
          f"multipliers={SENS_MULTIPLIERS}")
    rows: List[Dict] = []
    for mult in SENS_MULTIPLIERS:
        cap = bw_cap_star * mult
        for sched in SCHEDULERS_MAIN:
            for s in SEEDS_SENS:
                print(f"  - {sched:14s} seed={s} bw_cap={cap:.2e} (×{mult}) ...", flush=True)
                row = _run_one_arrival(sched, s, cap, fp_dir, cards, tasks, ARRIVAL)
                row["bw_multiplier"] = mult
                rows.append(row)
    raw_path = out_dir / "sensitivity_raw.csv"
    _write_csv(raw_path, rows)
    summary = _aggregate(rows, ["scheduler", "bw_cap", "bw_multiplier"])
    _write_csv(out_dir / "sensitivity_summary.csv", summary)
    print(f"[traffic.sens] wrote {raw_path}")


def _resolve_bw_cap(out_dir: Path, override: Optional[float]) -> float:
    if override is not None:
        return float(override)
    sel_path = out_dir / "calib_selection.csv"
    if sel_path.exists():
        with sel_path.open() as f:
            r = csv.reader(f)
            for row in r:
                if row and row[0] == "chosen_bw_cap" and row[1]:
                    return float(row[1])
    return BW_CAP_4_STAR


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("experiment", choices=["calib", "main", "sensitivity", "all"])
    ap.add_argument("--out-dir", default="data/q_traffic")
    ap.add_argument("--cards", type=int, default=CARDS_4)
    ap.add_argument("--tasks", type=int, default=TASKS_4)
    ap.add_argument("--bw-cap", type=float, default=None,
                    help="Override BW_CAP_4* for main/sensitivity (else read from calib_selection.csv)")
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    with tempfile.TemporaryDirectory(prefix="q_traffic_fp_") as tmp:
        fp_dir = _build_synthetic_fp_dir(Path(tmp) / "synth")
        if args.experiment in ("calib", "all"):
            run_calib(out_dir, fp_dir, cards=args.cards, tasks=args.tasks)
        if args.experiment in ("main", "all"):
            cap = _resolve_bw_cap(out_dir, args.bw_cap)
            run_main(out_dir, fp_dir, bw_cap=cap, cards=args.cards, tasks=args.tasks)
        if args.experiment in ("sensitivity", "all"):
            cap = _resolve_bw_cap(out_dir, args.bw_cap)
            run_sensitivity(out_dir, fp_dir, bw_cap_star=cap, cards=args.cards, tasks=args.tasks)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
