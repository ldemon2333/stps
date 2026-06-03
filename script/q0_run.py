"""Q0 experiment runner: end-to-end card-level load balance comparison.

Implements the experiments described in docs/Q0_TODO.md:
- main:    5 schedulers x 5 seeds at bursty arrival, fixed utilization (~70%)
- arrival: scheduler x arrival_mode {poisson, bursty, mixed}
- sweep:   scheduler x utilization {0.30, 0.50, 0.70, 0.85, 0.95}  (optional)

Outputs:
    data/q0/{exp}_raw.csv      — one row per (scheduler, seed, knob) run
    data/q0/{exp}_summary.csv  — mean / 95% CI half-width across seeds
    figures/q0/main_table.md   — Markdown summary for paper / docs
    figures/q0/scale16_table.md — Markdown summary for the 16-card scale run
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
from typing import Dict, List, Sequence, Tuple

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fingerprint import load_fingerprint, make_synthetic_fingerprint, save_fingerprint
from simulation.engine import run_simulation

# Pre-install a NullHandler on root so setup_logging (called inside run_simulation)
# skips its INFO StreamHandler/FileHandler install. Without this, every run prints
# tens of thousands of INFO lines to stderr, dwarfing the actual simulation work.
_root = logging.getLogger()
_root.setLevel(logging.ERROR)
if not _root.handlers:
    _root.addHandler(logging.NullHandler())
for name in ("simulation", "schedule", "util", "fingerprint", "simulation.engine"):
    logging.getLogger(name).setLevel(logging.ERROR)

# Q0 contrast set: baselines + full STPS (3 stages), NOT stps-spatial.
SCHEDULERS = ["rr", "bestfit", "drf", "p2c", "stps"]
SCHEDULER_LABELS = {
    "rr": "RR",
    "bestfit": "BestFit",
    "drf": "DRF",
    "p2c": "P2C",
    "stps": "STPS (full)",
}
SEEDS = [21, 42, 99, 123, 2024]
CARDS = 4
STEPS = 512
DEFAULT_TASKS = 800  # ~70% utilization (calibration shared with q1_run.py)
SCALE16_CARDS = 16
SCALE16_TASKS = 3200
SCALE16_FINGERPRINTS = [
    "synthetic_flat.npz",
    "synthetic_pulse_t8.npz",
    "synthetic_pulse_t16.npz",
    "synthetic_bursty.npz",
    "spikformer_cifar10.npz",
    "qkformer_cifar10.npz",
    "spikingresformer_ti_imagenet.npz",
]
SYNTHETIC_SPECS = [
    ("synthetic_flat.npz", 4, 1.05, 1, 11),
    ("synthetic_pulse_t8.npz", 8, 1.8, 1, 12),
    ("synthetic_pulse_t16.npz", 16, 2.5, 1, 13),
    ("synthetic_bursty.npz", 4, 3.8, 1, 14),
]
METRIC_KEYS = [
    "card_cv", "card_jfi", "card_lif", "max_min_ratio",
    "avg_load_imbalance", "completion_rate",
    "throughput", "throughput_excl_cold", "mean_cold_start",
    "p99_delay", "avg_delay",
    "p99_delay_excl_cold", "avg_delay_excl_cold",
    "avg_congestion_ratio", "congested_card_tick_frac",
    "mean_congestion_wait_ticks", "p95_congestion_wait_ticks",
    "avg_utilization", "peak_backlog",
    # docs/metrics.md §1b — time-domain per-card balance.
    "time_card_cv_mean", "time_card_cv_max",
    "time_card_jfi_mean",
    "time_card_lif_mean", "time_card_lif_max",
    "time_card_max_min_ratio_mean",
    "time_card_load_variance_mean",
    # Long-tail accounting (delay > p99_delay).
    "completed_tasks", "tail_count", "tail_frac",
    "max_delay", "avg_delay_above_p99",
    # Cold-start-excluded long-tail accounting (effective_delay > p99_excl_cold).
    "max_delay_excl_cold",
    "eff_tail_count", "eff_tail_frac", "eff_avg_delay_above_p99",
]


def _ci95(vals: Sequence[float]) -> float:
    n = len(vals)
    if n < 2:
        return 0.0
    return 1.96 * stdev(vals) / math.sqrt(n)


def _run_one(
    scheduler: str,
    seed: int,
    tasks: int,
    arrival_mode: str,
    fingerprint_dir: str,
    cards: int = CARDS,
) -> Tuple[Dict, Dict[str, np.ndarray]]:
    bw_cap = 9.0e5  # Phase A BW_CAP_4*; per-card cap, identical for 4-card and 16-card runs.
    metrics = run_simulation(
        scheduler=scheduler,
        cards=cards,
        tasks=tasks,
        steps=STEPS,
        seed=seed,
        arrival_mode=arrival_mode,
        fingerprint_dir=fingerprint_dir,
        bw_max=bw_cap,
        bw_cap=bw_cap,
        d_max=2,
        horizon=64,
        centrality_split_threshold=0.2,
        log_dir="log",
        data_dir="data/q0/_raw",
    )
    # Per-task delays (only completed tasks contribute).
    completed = [d for d in metrics.task_delays if d.completion_step >= 0]
    delays = np.asarray(
        [d.total_delay for d in completed if d.total_delay >= 0],
        dtype=np.int64,
    )
    eff_delays = np.asarray(
        [d.effective_delay for d in completed if d.effective_delay >= 0],
        dtype=np.int64,
    )
    cold_starts = np.asarray(
        [int(d.cold_start_ticks) for d in completed],
        dtype=np.int64,
    )
    if delays.size > 0:
        p99 = float(np.percentile(delays, 99))
        tail_mask = delays > p99
        tail_count = int(tail_mask.sum())
        tail_frac = float(tail_count) / float(delays.size)
        avg_delay_above_p99 = float(delays[tail_mask].mean()) if tail_count else 0.0
        max_delay = float(delays.max())
    else:
        p99 = 0.0
        tail_count = 0
        tail_frac = 0.0
        avg_delay_above_p99 = 0.0
        max_delay = 0.0
    if eff_delays.size > 0:
        eff_p99 = float(np.percentile(eff_delays, 99))
        eff_tail_mask = eff_delays > eff_p99
        eff_tail_count = int(eff_tail_mask.sum())
        eff_tail_frac = float(eff_tail_count) / float(eff_delays.size)
        eff_avg_above_p99 = (
            float(eff_delays[eff_tail_mask].mean()) if eff_tail_count else 0.0
        )
    else:
        eff_p99 = 0.0
        eff_tail_count = 0
        eff_tail_frac = 0.0
        eff_avg_above_p99 = 0.0
    row = {
        "scheduler": scheduler,
        "seed": seed,
        "cards": cards,
        "tasks": tasks,
        "arrival_mode": arrival_mode,
        "bw_cap": bw_cap,
        "card_cv": metrics.avg_card_cv,
        "card_jfi": metrics.avg_card_jfi,
        "card_lif": metrics.avg_card_lif,
        "max_min_ratio": metrics.avg_max_min_ratio,
        "avg_load_imbalance": metrics.avg_load_imbalance,
        "completion_rate": metrics.completion_rate,
        "throughput": metrics.throughput,
        "throughput_excl_cold": metrics.throughput_excl_cold,
        "mean_cold_start": metrics.mean_cold_start,
        "p99_delay": metrics.p99_delay,
        "avg_delay": metrics.avg_delay,
        "p99_delay_excl_cold": metrics.p99_delay_excl_cold,
        "avg_delay_excl_cold": metrics.avg_delay_excl_cold,
        "avg_congestion_ratio": metrics.avg_congestion_ratio,
        "congested_card_tick_frac": metrics.congested_card_tick_frac,
        "mean_congestion_wait_ticks": metrics.mean_congestion_wait_ticks,
        "p95_congestion_wait_ticks": metrics.p95_congestion_wait_ticks,
        "avg_utilization": metrics.avg_utilization,
        "peak_backlog": metrics.peak_backlog,
        # docs/metrics.md §1b
        "time_card_cv_mean": metrics.time_card_cv_mean,
        "time_card_cv_max": metrics.time_card_cv_max,
        "time_card_jfi_mean": metrics.time_card_jfi_mean,
        "time_card_lif_mean": metrics.time_card_lif_mean,
        "time_card_lif_max": metrics.time_card_lif_max,
        "time_card_max_min_ratio_mean": metrics.time_card_max_min_ratio_mean,
        "time_card_load_variance_mean": metrics.time_card_load_variance_mean,
        # Long-tail accounting: how many completed tasks have delay > p99.
        "completed_tasks": int(delays.size),
        "tail_count": tail_count,
        "tail_frac": tail_frac,
        "max_delay": max_delay,
        "avg_delay_above_p99": avg_delay_above_p99,
        # Cold-start-excluded long-tail accounting.
        "max_delay_excl_cold": float(eff_delays.max()) if eff_delays.size else 0.0,
        "eff_tail_count": eff_tail_count,
        "eff_tail_frac": eff_tail_frac,
        "eff_avg_delay_above_p99": eff_avg_above_p99,
    }
    return row, {
        "delay": delays,
        "delay_excl_cold": eff_delays,
        "cold_start": cold_starts,
    }


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
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


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


def _build_synthetic_fp_dir(parent: Path) -> str:
    """Build a capacity-fit synthetic-only fingerprint dir for Q0.

    The generated fingerprints preserve the repository-level synthetic temporal
    shapes while keeping each task within the default card's neuron capacity.
    """
    parent.mkdir(parents=True, exist_ok=True)
    for name, T, beta, K, seed in SYNTHETIC_SPECS:
        fp = make_synthetic_fingerprint(
            beta_target=beta,
            K=K,
            T=T,
            V=16,
            neuron_count=2_000_000,
            state_size_mb=32.0,
            complexity_ratio=1.0,
            e_mean=200000.0,
            seed=seed,
            meta={"source": "q0-synthetic", "beta_target": str(beta)},
        )
        save_fingerprint(parent / name, fp)
    return str(parent)


def _build_mixed_fp_dir(parent: Path, source_dir: Path = ROOT / "npz") -> str:
    """Build the 16-card mixed synthetic + real fingerprint directory."""
    parent.mkdir(parents=True, exist_ok=True)
    for name in SCALE16_FINGERPRINTS:
        src = source_dir / name
        if not src.exists():
            raise FileNotFoundError(f"Q0 scale16 fingerprint missing: {src}")
        fp = load_fingerprint(src)
        save_fingerprint(parent / name, fp)
    return str(parent)


def plot_long_tail(
    delay_rows: List[Dict],
    out_dir: Path,
    prefix: str,
    schedulers: Sequence[str],
) -> None:
    """Draw long-tail delay distributions for a 16-card experiment.

    For every arrival mode found in `delay_rows`, produces two figures:
      - {prefix}_long_tail_{arrival}.pdf: per-scheduler histogram panel grid
        (x = delay ticks, y = task count, log y) with the per-scheduler
        pooled P99 marked and a "#(>P99) / N" annotation.
      - {prefix}_long_tail_ccdf_{arrival}.pdf: single panel survival
        function P[Delay > d] for all schedulers overlaid (log-log axes).
    """
    if not delay_rows:
        return
    # Lazy import to keep script import cheap when only running tables.
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    out_dir.mkdir(parents=True, exist_ok=True)

    # Bucket delays by (arrival, scheduler) across all seeds.
    by_arrival: Dict[str, Dict[str, List[int]]] = {}
    for r in delay_rows:
        by_arrival.setdefault(r["arrival_mode"], {}).setdefault(r["scheduler"], []).append(int(r["delay"]))

    palette = {
        "rr": "#1f77b4",
        "bestfit": "#2ca02c",
        "drf": "#9467bd",
        "p2c": "#ff7f0e",
        "stps": "#d62728",
        "stps-la": "#8c564b",
    }

    for arrival, per_sched in by_arrival.items():
        present = [s for s in schedulers if s in per_sched]
        if not present:
            continue
        # Shared bin edges across schedulers for visual comparability.
        all_delays = np.concatenate([np.asarray(per_sched[s], dtype=np.int64) for s in present])
        if all_delays.size == 0:
            continue
        d_max = int(all_delays.max())
        # 1-tick bins up to max delay; cap bin count to keep file size sane.
        n_bins = min(120, max(20, d_max // 2))
        bins = np.linspace(0, max(d_max, 1) + 1, n_bins + 1)

        # ---------- Figure A: per-scheduler histogram grid ----------
        n_panels = len(present)
        n_cols = min(3, n_panels)
        n_rows = math.ceil(n_panels / n_cols)
        fig, axes = plt.subplots(
            n_rows, n_cols,
            figsize=(4.2 * n_cols, 2.8 * n_rows),
            dpi=200,
            sharex=True,
            sharey=True,
        )
        axes = np.atleast_1d(axes).flatten()
        for ax, sched in zip(axes, present):
            delays = np.asarray(per_sched[sched], dtype=np.int64)
            p99 = float(np.percentile(delays, 99)) if delays.size else 0.0
            tail = int((delays > p99).sum())
            color = palette.get(sched, "#444444")
            # log=True makes hist set bar bottom to a small positive value,
            # so empty bins don't fill the entire log panel with shading.
            ax.hist(delays, bins=bins, color=color, alpha=0.85,
                    edgecolor="none", log=True)
            ax.axvline(p99, color="black", linestyle="--", linewidth=1.0, alpha=0.7)
            ax.set_ylim(0.5, max(2.0, delays.size))
            ax.set_title(
                f"{SCHEDULER_LABELS.get(sched, sched)} "
                f"(P99={p99:.0f}, tail={tail}/{delays.size}, "
                f"{tail/delays.size*100:.2f}%)",
                fontsize=9,
            )
            ax.spines["top"].set_visible(False)
            ax.spines["right"].set_visible(False)
            ax.set_xlabel("Task delay (ticks)", fontsize=9)
            ax.set_ylabel("# tasks (log)", fontsize=9)
        for ax in axes[len(present):]:
            ax.set_visible(False)
        fig.suptitle(
            f"Long-tail delay distribution — 16 cards, arrival={arrival}, "
            f"{len(SEEDS)} seeds pooled",
            fontsize=11,
        )
        fig.tight_layout(rect=(0, 0, 1, 0.96))
        path_a = out_dir / f"{prefix}_long_tail_{arrival}.pdf"
        fig.savefig(path_a, bbox_inches="tight")
        fig.savefig(path_a.with_suffix(".png"), bbox_inches="tight")
        plt.close(fig)
        print(f"[Q0] wrote {path_a}")

        # ---------- Figure B: CCDF overlay ----------
        fig2, ax2 = plt.subplots(figsize=(6.0, 4.0), dpi=200)
        for sched in present:
            delays = np.sort(np.asarray(per_sched[sched], dtype=np.int64))
            if delays.size == 0:
                continue
            n = delays.size
            # CCDF: y[i] = P[X > delays[i]] = (n - i - 1) / n  (right-continuous).
            ccdf = (n - np.arange(1, n + 1)) / n
            # Filter out 0 (last point) to allow log-y.
            mask = ccdf > 0
            ax2.step(
                delays[mask], ccdf[mask],
                where="post",
                color=palette.get(sched, "#444444"),
                label=SCHEDULER_LABELS.get(sched, sched),
                linewidth=1.6,
                alpha=0.9,
            )
        ax2.set_xscale("log")
        ax2.set_yscale("log")
        ax2.set_xlabel("Task delay d (ticks, log)")
        ax2.set_ylabel("P[Delay > d]  (log)")
        ax2.set_title(
            f"Delay survival function — 16 cards, arrival={arrival}",
            fontsize=11,
        )
        ax2.axhline(0.01, color="gray", linestyle=":", linewidth=0.8, alpha=0.7)
        ax2.text(
            ax2.get_xlim()[1], 0.01, "  P99 = 1%",
            color="gray", fontsize=8, va="center", ha="right",
        )
        ax2.spines["top"].set_visible(False)
        ax2.spines["right"].set_visible(False)
        ax2.legend(loc="lower left", frameon=False, fontsize=9)
        fig2.tight_layout()
        path_b = out_dir / f"{prefix}_long_tail_ccdf_{arrival}.pdf"
        fig2.savefig(path_b, bbox_inches="tight")
        fig2.savefig(path_b.with_suffix(".png"), bbox_inches="tight")
        plt.close(fig2)
        print(f"[Q0] wrote {path_b}")


def run_main(out_dir: Path, fp_dir: str) -> List[Dict]:
    print(f"[Q0.main] {len(SCHEDULERS)} schedulers x {len(SEEDS)} seeds, "
          f"cards={CARDS}, tasks={DEFAULT_TASKS}, steps={STEPS}, "
          f"arrival=bursty, fp=mixed, bw_cap=9e5, d_max=2")
    rows = []
    for sched in SCHEDULERS:
        for seed in SEEDS:
            print(f"  - {sched:10s} seed={seed} ...", flush=True)
            row, _ = _run_one(sched, seed, DEFAULT_TASKS, "bursty", fp_dir)
            row["fingerprint_set"] = "mixed"
            rows.append(row)
    _write_csv(out_dir / "main_raw.csv", rows)
    summary = _aggregate(rows, ["scheduler"])
    _write_csv(out_dir / "main_summary.csv", summary)
    return summary


def run_arrival(out_dir: Path, fp_dir: str) -> List[Dict]:
    modes = ["poisson", "bursty", "mixed"]
    print(f"[Q0.arrival] {len(SCHEDULERS)} schedulers x {len(modes)} modes x "
          f"{len(SEEDS)} seeds, cards={CARDS}, tasks={DEFAULT_TASKS}, fp=mixed")
    rows = []
    for sched in SCHEDULERS:
        for mode in modes:
            for seed in SEEDS:
                print(f"  - {sched:10s} arrival={mode:7s} seed={seed} ...", flush=True)
                row, _ = _run_one(sched, seed, DEFAULT_TASKS, mode, fp_dir)
                row["fingerprint_set"] = "mixed"
                rows.append(row)
    _write_csv(out_dir / "arrival_raw.csv", rows)
    summary = _aggregate(rows, ["scheduler", "arrival_mode"])
    _write_csv(out_dir / "arrival_summary.csv", summary)
    return summary


def run_sweep(out_dir: Path, fp_dir: str) -> List[Dict]:
    util_to_tasks = {0.30: 320, 0.50: 560, 0.70: 800, 0.85: 1020, 0.95: 1100}
    print(f"[Q0.sweep] {len(SCHEDULERS)} schedulers x {len(util_to_tasks)} util x "
          f"{len(SEEDS)} seeds, arrival=bursty")
    rows = []
    for sched in SCHEDULERS:
        for util, tasks in util_to_tasks.items():
            for seed in SEEDS:
                print(f"  - {sched:10s} util={util:.2f} seed={seed} ...", flush=True)
                row, _ = _run_one(sched, seed, tasks, "bursty", fp_dir)
                row["utilization"] = util
                rows.append(row)
    _write_csv(out_dir / "sweep_raw.csv", rows)
    summary = _aggregate(rows, ["scheduler", "utilization"])
    _write_csv(out_dir / "sweep_summary.csv", summary)
    return summary


def run_scale16(out_dir: Path, fp_dir: str) -> List[Dict]:
    modes = ["poisson", "bursty"]
    print(f"[Q0.scale16] {len(SCHEDULERS)} schedulers x {len(modes)} modes x "
          f"{len(SEEDS)} seeds, cards={SCALE16_CARDS}, tasks={SCALE16_TASKS}, "
          f"steps={STEPS}, fp=mixed")
    rows = []
    delay_rows: List[Dict] = []
    for sched in SCHEDULERS:
        for mode in modes:
            for seed in SEEDS:
                print(f"  - {sched:10s} arrival={mode:7s} seed={seed} ...", flush=True)
                row, delay_dict = _run_one(
                    sched,
                    seed,
                    SCALE16_TASKS,
                    mode,
                    fp_dir,
                    cards=SCALE16_CARDS,
                )
                row["fingerprint_set"] = "mixed"
                rows.append(row)
                # Long-tail uses original total_delay (cold-start included),
                # per user spec: p99/tail accounting keeps the wall-clock view.
                delays = delay_dict["delay"]
                eff_delays = delay_dict["delay_excl_cold"]
                cold_starts = delay_dict["cold_start"]
                p99 = float(np.percentile(delays, 99)) if delays.size else 0.0
                # Keep the per-task dump aligned across all three columns.
                for d, ed, cs in zip(
                    delays.tolist(), eff_delays.tolist(), cold_starts.tolist()
                ):
                    delay_rows.append({
                        "scheduler": sched,
                        "arrival_mode": mode,
                        "seed": seed,
                        "delay": int(d),
                        "delay_excl_cold": int(ed),
                        "cold_start": int(cs),
                        "p99_delay_run": p99,
                    })
    _write_csv(out_dir / "scale16_raw.csv", rows)
    _write_csv(out_dir / "scale16_task_delays.csv", delay_rows)
    summary = _aggregate(rows, ["fingerprint_set", "arrival_mode", "scheduler"])
    _write_csv(out_dir / "scale16_summary.csv", summary)
    # Long-tail figures stay on the original total_delay (user spec: long-tail
    # analysis keeps the cold-start ticks; only throughput / exec-time metrics
    # have the cold-start-excluded view).
    plot_long_tail(
        delay_rows,
        out_dir=Path("figures/q0"),
        prefix="scale16",
        schedulers=SCHEDULERS,
    )
    return summary


def write_main_markdown(summary: List[Dict], out: Path) -> None:
    by_sched = {r["scheduler"]: r for r in summary}
    lines = [
        "# Q0 Main Table — End-to-End Card-Level Load Balance",
        "",
        f"Setup: cards={CARDS}, tasks={DEFAULT_TASKS}, steps={STEPS}, "
        f"arrival=bursty, seeds={SEEDS}",
        "",
        "Fingerprint dir: synthetic-only (flat / pulse_t8 / pulse_t16 / bursty), "
        "to avoid scale-mixing with real-model spike counts.",
        "",
        "Values are `mean ± 95% CI half-width` across seeds.",
        "",
        "| Scheduler | card-CV ↓ | card-JFI ↑ | card-LIF ↓ | Max/Min ↓ |",
        "|---|---|---|---|---|",
    ]
    for sched in SCHEDULERS:
        r = by_sched.get(sched)
        if r is None:
            continue
        lines.append(
            f"| {SCHEDULER_LABELS[sched]} | "
            f"{r['card_cv_mean']:.4f} ± {r['card_cv_ci95']:.4f} | "
            f"{r['card_jfi_mean']:.4f} ± {r['card_jfi_ci95']:.4f} | "
            f"{r['card_lif_mean']:.4f} ± {r['card_lif_ci95']:.4f} | "
            f"{r['max_min_ratio_mean']:.3f} ± {r['max_min_ratio_ci95']:.3f} |"
        )

    stps = by_sched.get("stps")
    if stps is not None:
        lines += ["", "## Headline numbers (STPS vs baselines)", ""]
        for sched in ["rr", "bestfit", "drf", "p2c"]:
            r = by_sched.get(sched)
            if r is None or r["card_cv_mean"] <= 0:
                continue
            cv_drop = (r["card_cv_mean"] - stps["card_cv_mean"]) / r["card_cv_mean"]
            jfi_gain = stps["card_jfi_mean"] - r["card_jfi_mean"]
            lif_drop = ((r["card_lif_mean"] - stps["card_lif_mean"]) / r["card_lif_mean"]
                        if r["card_lif_mean"] > 0 else 0.0)
            lines.append(
                f"- vs {SCHEDULER_LABELS[sched]:7s}: CV {cv_drop*100:+.1f}%, "
                f"JFI {jfi_gain:+.4f}, LIF {lif_drop*100:+.1f}%"
            )

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n")
    print(f"[Q0] wrote {out}")


def write_scale16_markdown(summary: List[Dict], out: Path) -> None:
    rows = sorted(summary, key=lambda r: (r["arrival_mode"], SCHEDULERS.index(r["scheduler"])))
    lines = [
        "# Q0 Scale16 Table — 16-Card Mixed-Fingerprint Run",
        "",
        f"Setup: cards={SCALE16_CARDS}, tasks={SCALE16_TASKS}, steps={STEPS}, "
        f"arrival={{poisson, bursty}}, seeds={SEEDS}",
        "",
        "Fingerprint set: mixed synthetic + real "
        "(flat / pulse_t8 / pulse_t16 / bursty / spikformer / qkformer / spikingresformer).",
        "",
        "Values are `mean ± 95% CI half-width` across seeds.",
        "",
        "| Arrival | Scheduler | card-CV ↓ | card-JFI ↑ | card-LIF ↓ | Max/Min ↓ | Throughput ↑ | p99 delay ↓ |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        sched = r["scheduler"]
        lines.append(
            f"| {r['arrival_mode']} | {SCHEDULER_LABELS[sched]} | "
            f"{r['card_cv_mean']:.4f} ± {r['card_cv_ci95']:.4f} | "
            f"{r['card_jfi_mean']:.4f} ± {r['card_jfi_ci95']:.4f} | "
            f"{r['card_lif_mean']:.4f} ± {r['card_lif_ci95']:.4f} | "
            f"{r['max_min_ratio_mean']:.3f} ± {r['max_min_ratio_ci95']:.3f} | "
            f"{r['throughput_mean']:.3f} ± {r['throughput_ci95']:.3f} | "
            f"{r['p99_delay_mean']:.3f} ± {r['p99_delay_ci95']:.3f} |"
        )

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n")
    print(f"[Q0] wrote {out}")


def run_la_main(out_dir: Path, fp_dir: str) -> List[Dict]:
    """docs/Q0_result.md §7 — load-aware extension contrast (4-card).

    Reduced contrast set: bestfit (best baseline) + stps (current) + stps-la (改动 A+D).
    """
    schedulers = ["bestfit", "stps", "stps-la"]
    print(f"[Q0.la-main] {len(schedulers)} schedulers x {len(SEEDS)} seeds, "
          f"cards={CARDS}, tasks={DEFAULT_TASKS}, steps={STEPS}, arrival=bursty, fp=mixed")
    rows = []
    for sched in schedulers:
        for seed in SEEDS:
            print(f"  - {sched:10s} seed={seed} ...", flush=True)
            row, _ = _run_one(sched, seed, DEFAULT_TASKS, "bursty", fp_dir)
            row["fingerprint_set"] = "mixed"
            rows.append(row)
    _write_csv(out_dir / "la_main_raw.csv", rows)
    summary = _aggregate(rows, ["scheduler"])
    _write_csv(out_dir / "la_main_summary.csv", summary)
    return summary


def run_la_scale16(out_dir: Path, fp_dir: str) -> List[Dict]:
    schedulers = ["bestfit", "stps", "stps-la"]
    modes = ["poisson", "bursty"]
    print(f"[Q0.la-scale16] {len(schedulers)} schedulers x {len(modes)} modes x "
          f"{len(SEEDS)} seeds, cards={SCALE16_CARDS}, tasks={SCALE16_TASKS}, fp=mixed")
    rows = []
    delay_rows: List[Dict] = []
    for sched in schedulers:
        for mode in modes:
            for seed in SEEDS:
                print(f"  - {sched:10s} arrival={mode:7s} seed={seed} ...", flush=True)
                row, delay_dict = _run_one(
                    sched, seed, SCALE16_TASKS, mode, fp_dir, cards=SCALE16_CARDS,
                )
                row["fingerprint_set"] = "mixed"
                rows.append(row)
                delays = delay_dict["delay"]
                eff_delays = delay_dict["delay_excl_cold"]
                cold_starts = delay_dict["cold_start"]
                p99 = float(np.percentile(delays, 99)) if delays.size else 0.0
                for d, ed, cs in zip(
                    delays.tolist(), eff_delays.tolist(), cold_starts.tolist()
                ):
                    delay_rows.append({
                        "scheduler": sched,
                        "arrival_mode": mode,
                        "seed": seed,
                        "delay": int(d),
                        "delay_excl_cold": int(ed),
                        "cold_start": int(cs),
                        "p99_delay_run": p99,
                    })
    _write_csv(out_dir / "la_scale16_raw.csv", rows)
    _write_csv(out_dir / "la_scale16_task_delays.csv", delay_rows)
    summary = _aggregate(rows, ["fingerprint_set", "arrival_mode", "scheduler"])
    _write_csv(out_dir / "la_scale16_summary.csv", summary)
    plot_long_tail(
        delay_rows,
        out_dir=Path("figures/q0"),
        prefix="la_scale16",
        schedulers=schedulers,
    )
    return summary


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("experiment", choices=["main", "arrival", "sweep", "scale16", "all", "la-main", "la-scale16", "la-all"])
    ap.add_argument("--out-dir", default="data/q0")
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    with tempfile.TemporaryDirectory(prefix="q0_fp_") as tmp:
        mixed_fp_dir = _build_mixed_fp_dir(Path(tmp) / "mixed")
        if args.experiment in ("main", "all"):
            summary = run_main(out_dir, mixed_fp_dir)
            write_main_markdown(summary, Path("figures/q0/main_table.md"))
        if args.experiment in ("arrival", "all"):
            run_arrival(out_dir, mixed_fp_dir)
        if args.experiment in ("sweep", "all"):
            run_sweep(out_dir, mixed_fp_dir)
        if args.experiment in ("scale16", "all"):
            summary = run_scale16(out_dir, mixed_fp_dir)
            write_scale16_markdown(summary, Path("figures/q0/scale16_table.md"))
        if args.experiment in ("la-main", "la-all"):
            run_la_main(out_dir, mixed_fp_dir)
        if args.experiment in ("la-scale16", "la-all"):
            run_la_scale16(out_dir, mixed_fp_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
