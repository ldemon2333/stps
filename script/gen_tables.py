"""Regenerate all paper result tables from the freshly-computed summary CSVs.

Reads the Q0 / Q1 / Q2 summary CSVs under data/ and emits markdown tables that
mirror the nine tables in `SNN schedule/article.tex`:

  tab:q0_main           -> data/q0/{arrival,scale16}_summary.csv
  tab:q0_cold           -> data/q0/scale16_summary.csv
  tab:q1_util           -> data/q1/sweep_summary.csv
  tab:q1_mix            -> data/q1/mix_summary.csv
  tab:q2_phase          -> data/q2/main4_bw9e5_d2_summary.csv
  tab:q2_dmax           -> data/q2/main4_bw9e5_d{2,4,8,16}_t400_summary.csv
  tab:q2_regime_profile -> A/B/C summaries
  tab:q2_regime_cv      -> A/B/C summaries

Output: SNN schedule/experiment_regen.md
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUT = ROOT / "SNN schedule" / "experiment_regen.md"

SCHED_LABEL = {
    "rr": "RR", "bestfit": "BestFit", "drf": "DRF", "p2c": "P2C",
    "stps": "STPS", "stps-spatial": "STPS-spatial",
}
Q0_ORDER = ["rr", "bestfit", "drf", "p2c", "stps"]


def read_csv(path: Path) -> List[Dict[str, str]]:
    if not path.exists():
        return []
    with path.open() as f:
        return list(csv.DictReader(f))


def f(row: Dict[str, str], col: str) -> Optional[float]:
    v = row.get(col)
    if v is None or v == "":
        return None
    try:
        return float(v)
    except ValueError:
        return None


# ---------------------------------------------------------------- Q0 main/cold
def q0_main(lines: List[str]) -> None:
    arr = read_csv(DATA / "q0" / "arrival_summary.csv")
    s16 = read_csv(DATA / "q0" / "scale16_summary.csv")
    lines += [
        "## tab:q0_main — End-to-end comparison (5-seed means)",
        "",
        "| Scale | Arrival | Scheduler | card-CV ↓ | JFI ↑ | max/min ↓ | tput ↑ | cong.ratio ↓ | cong.wait ↓ | p99 ↓ |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]

    def row_for(scale: str, arrival: str, sched: str, r: Dict[str, str]) -> str:
        return (f"| {scale} | {arrival} | {SCHED_LABEL[sched]} | "
                f"{f(r,'card_cv_mean'):.3f} | {f(r,'card_jfi_mean'):.3f} | "
                f"{f(r,'max_min_ratio_mean'):.2f} | {f(r,'throughput_mean'):.3f} | "
                f"{f(r,'avg_congestion_ratio_mean'):.4f} | "
                f"{f(r,'mean_congestion_wait_ticks_mean'):.2f} | "
                f"{f(r,'p99_delay_mean'):.1f} |")

    for arrival in ("poisson", "bursty", "mixed"):
        for sched in Q0_ORDER:
            r = next((x for x in arr if x["scheduler"] == sched
                      and x["arrival_mode"] == arrival), None)
            if r:
                lines.append(row_for("4 cards", arrival, sched, r))
    for arrival in ("poisson", "bursty", "mixed"):
        for sched in Q0_ORDER:
            r = next((x for x in s16 if x["scheduler"] == sched
                      and x["arrival_mode"] == arrival), None)
            if r:
                lines.append(row_for("16 cards", arrival, sched, r))
    lines.append("")


def q0_cold(lines: List[str]) -> None:
    s16 = read_csv(DATA / "q0" / "scale16_summary.csv")
    if not s16:
        return
    lines += [
        "## tab:q0_cold — Throughput decomposition at 16 cards",
        "",
        "| Arrival | Scheduler | mean cold-start | tput | tput (excl. cold) |",
        "|---|---|---|---|---|",
    ]
    for arrival in ("poisson", "bursty"):
        rows = [x for x in s16 if x["arrival_mode"] == arrival]
        baselines = [x for x in rows if x["scheduler"] != "stps"]
        if not baselines:
            continue
        best = max(baselines, key=lambda x: f(x, "throughput_mean") or 0)
        stps = next((x for x in rows if x["scheduler"] == "stps"), None)
        for tag, r in ((f"best baseline ({SCHED_LABEL[best['scheduler']]})", best),
                       ("STPS", stps)):
            if r is None:
                continue
            lines.append(
                f"| {arrival} | {tag} | {f(r,'mean_cold_start_mean'):.2f} | "
                f"{f(r,'throughput_mean'):.3f} | "
                f"{f(r,'throughput_excl_cold_mean'):.3f} |")
    lines.append("")


# ------------------------------------------------------------------ Q1 tables
def q1_util(lines: List[str]) -> None:
    sw = read_csv(DATA / "q1" / "sweep_summary.csv")
    if not sw:
        return
    order = ["rr", "bestfit", "drf", "p2c", "stps-spatial"]
    utils = sorted({f(x, "utilization") for x in sw if f(x, "utilization") is not None})
    lines += [
        "## tab:q1_util — Stage A utilisation sweep (card-CV ↓)",
        "",
        "| Util. | RR | BestFit | DRF | P2C | STPS-A |",
        "|---|---|---|---|---|---|",
    ]
    for u in utils:
        cells = []
        for sched in order:
            r = next((x for x in sw if x["scheduler"] == sched
                      and f(x, "utilization") == u), None)
            cells.append(f"{f(r,'card_cv_mean'):.3f}" if r else "—")
        lines.append(f"| {u:.2f} | " + " | ".join(cells) + " |")
    lines.append("")


def q1_mix(lines: List[str]) -> None:
    mx = read_csv(DATA / "q1" / "mix_summary.csv")
    if not mx:
        return
    order = ["rr", "bestfit", "drf", "p2c", "stps-spatial"]
    ratios = [(100, 0), (75, 25), (50, 50), (25, 75), (0, 100)]
    lines += [
        "## tab:q1_mix — Stage A fingerprint-composition sweep (card-CV ↓)",
        "",
        "| Flat / bursty | RR | BestFit | DRF | P2C | STPS-A |",
        "|---|---|---|---|---|---|",
    ]
    for flat, burst in ratios:
        cells = []
        for sched in order:
            r = next((x for x in mx if x["scheduler"] == sched
                      and int(float(x.get("flat_pct", -1))) == flat
                      and int(float(x.get("bursty_pct", -1))) == burst), None)
            cells.append(f"{f(r,'card_cv_mean'):.3f}" if r else "—")
        lines.append(f"| {flat} / {burst} | " + " | ".join(cells) + " |")
    lines.append("")


# ------------------------------------------------------------------ Q2 helpers
Q2_PAIRS = [
    ("RR", "rr", "rr-phase"),
    ("BestFit", "bestfit", "bestfit-phase"),
    ("DRF", "drf", "drf-phase"),
    ("P2C", "p2c", "p2c-phase"),
    ("STPS-spatial", "stps-spatial", "stps"),
]


def q2_index(rows: List[Dict[str, str]]) -> Dict[Tuple[str, str, str], Dict[str, str]]:
    """Index by (arrival, base_algo, phase_enabled)."""
    idx = {}
    for r in rows:
        idx[(r["arrival_mode"], r["base_algo"], r["phase_enabled"])] = r
    return idx


def q2_phase(lines: List[str]) -> None:
    rows = read_csv(DATA / "q2" / "main4_bw9e5_d2_summary.csv")
    if not rows:
        return
    idx = q2_index(rows)
    lines += [
        "## tab:q2_phase — Stage decomposition (4 cards, bw=9e5, d_max=2)",
        "",
        "| Arrival | Policy | card-CV | tput | cong.ratio | cong.wait | p99 | mean offset |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for arrival in ("poisson", "bursty"):
        for label, base, phase in Q2_PAIRS:
            off = idx.get((arrival, label, "False"))
            on = idx.get((arrival, label, "True"))
            base_name = f"{label} base" if label != "STPS-spatial" else "stps-spatial"
            on_name = f"{label} +ps" if label != "STPS-spatial" else "stps-spatial+ps"
            for nm, r in ((base_name, off), (on_name, on)):
                if r is None:
                    continue
                lines.append(
                    f"| {arrival} | {nm} | {f(r,'card_cv_mean'):.3f} | "
                    f"{f(r,'throughput_mean'):.3f} | "
                    f"{f(r,'avg_congestion_ratio_mean'):.3f} | "
                    f"{f(r,'mean_congestion_wait_ticks_mean'):.2f} | "
                    f"{f(r,'p99_delay_mean'):.1f} | "
                    f"{f(r,'mean_start_offset_mean'):.2f} |")
    lines.append("")


def q2_dmax(lines: List[str]) -> None:
    d_list = [2, 4, 8, 16]
    data = {}
    for d in d_list:
        rows = read_csv(DATA / "q2" / f"main4_bw9e5_d{d}_t400_summary.csv")
        if rows:
            data[d] = q2_index(rows)
    if not data:
        return
    lines += [
        "## tab:q2_dmax — D_max sweep, light regime C (bw=9e5, tasks=400)",
        "",
    ]
    for arrival in ("poisson", "bursty"):
        lines += [
            f"### arrival = {arrival}",
            "",
            "| Base Algo | D_max | tput ↑ | CV ↓ | JFI ↑ | LIF ↓ | Max/Min ↓ | p99 ↓ | cong.ratio ↓ | cong.wait ↓ | mean offset |",
            "|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        for label, base, phase in Q2_PAIRS:
            # phase=off baseline shared; take from D=16 file
            if 16 in data:
                off = data[16].get((arrival, label, "False"))
                if off:
                    lines.append(
                        f"| {label} | off | {f(off,'throughput_mean'):.3f} | "
                        f"{f(off,'card_cv_mean'):.4f} | {f(off,'card_jfi_mean'):.3f} | "
                        f"{f(off,'card_lif_mean'):.3f} | {f(off,'max_min_ratio_mean'):.2f} | "
                        f"{f(off,'p99_delay_mean'):.1f} | "
                        f"{f(off,'avg_congestion_ratio_mean'):.4f} | "
                        f"{f(off,'mean_congestion_wait_ticks_mean'):.2f} | 0.00 |")
            for d in d_list:
                if d not in data:
                    continue
                on = data[d].get((arrival, label, "True"))
                if on is None:
                    continue
                lines.append(
                    f"| {label} | {d} | {f(on,'throughput_mean'):.3f} | "
                    f"{f(on,'card_cv_mean'):.4f} | {f(on,'card_jfi_mean'):.3f} | "
                    f"{f(on,'card_lif_mean'):.3f} | {f(on,'max_min_ratio_mean'):.2f} | "
                    f"{f(on,'p99_delay_mean'):.1f} | "
                    f"{f(on,'avg_congestion_ratio_mean'):.4f} | "
                    f"{f(on,'mean_congestion_wait_ticks_mean'):.2f} | "
                    f"{f(on,'mean_start_offset_mean'):.2f} |")
        lines.append("")


def q2_regime(lines: List[str]) -> None:
    regimes = {
        "A: binding (9e5, 800)": DATA / "q2" / "main4_bw9e5_d16_summary.csv",
        "B: non-binding (5e6, 800)": DATA / "q2" / "main4_bw5e6_d16_summary.csv",
        "C: light (9e5, 400)": DATA / "q2" / "main4_bw9e5_d16_t400_summary.csv",
    }
    idx = {}
    for name, path in regimes.items():
        rows = read_csv(path)
        if rows:
            idx[name] = q2_index(rows)
    if not idx:
        return
    names = list(idx.keys())

    # Profile table
    lines += [
        "## tab:q2_regime_profile — Cross-regime cap-binding profile (baselines)",
        "",
        "| Metric | " + " | ".join(names) + " |",
        "|---" * (len(names) + 1) + "|",
    ]

    def base_range(reg: Dict, col: str, fmt: str) -> str:
        vals = [f(reg.get((arr, lbl, "False"), {}), col)
                for arr in ("poisson", "bursty")
                for lbl, *_ in Q2_PAIRS]
        vals = [v for v in vals if v is not None]
        if not vals:
            return "—"
        return f"{min(vals):{fmt}}–{max(vals):{fmt}}"

    for metric, col, fmt in (
        ("Baseline cong. ratio", "avg_congestion_ratio_mean", ".3f"),
        ("Baseline card-CV", "card_cv_mean", ".3f"),
        ("Baseline p99", "p99_delay_mean", ".0f"),
    ):
        cells = [base_range(idx[n], col, fmt) for n in names]
        lines.append(f"| {metric} | " + " | ".join(cells) + " |")
    # offset-infeasible ≈ reject_rate_bw on phase=on
    inf_cells = []
    for n in names:
        vals = [f(idx[n].get((arr, lbl, "True"), {}), "reject_rate_bw_mean")
                for arr in ("poisson", "bursty") for lbl, *_ in Q2_PAIRS]
        vals = [v for v in vals if v is not None]
        inf_cells.append(f"{min(vals):.2f}–{max(vals):.2f}" if vals else "—")
    lines.append("| Offset-infeasible (+ps) | " + " | ".join(inf_cells) + " |")
    lines.append("")

    # Delta card-CV table
    lines += [
        "## tab:q2_regime_cv — Δcard-CV under +ps  ((off−on)/off ×100, + = phase-shift reduces CV)",
        "",
        "| Arrival | Policy | " + " | ".join(names) + " |",
        "|---" * (len(names) + 2) + "|",
    ]
    for arrival in ("poisson", "bursty"):
        for label, base, phase in Q2_PAIRS:
            cells = []
            for n in names:
                off = idx[n].get((arrival, label, "False"))
                on = idx[n].get((arrival, label, "True"))
                o = f(off or {}, "card_cv_mean")
                v = f(on or {}, "card_cv_mean")
                if o and v is not None and o != 0:
                    cells.append(f"{(o - v) / o * 100:+.1f}")
                else:
                    cells.append("—")
            lines.append(f"| {arrival} | {label} | " + " | ".join(cells) + " |")
    lines.append("")


def main() -> int:
    lines: List[str] = [
        "# Regenerated experiment tables",
        "",
        "Auto-generated by `script/gen_tables.py` from the Q0/Q1/Q2 summary CSVs.",
        "All values are 5-seed means (seeds 21/42/99/123/2024). Mirrors the nine",
        "tables in `article.tex`.",
        "",
        "## Provenance & validation",
        "",
        "Regenerated on the current codebase under the bounded-FIFO NoC model "
        "(`/usr/bin/python3`, 5 seeds, steps=512).",
        "",
        "| Table | Source | vs. `article.tex` |",
        "|---|---|---|",
        "| tab:q0_main | data/q0/{arrival,scale16}_summary.csv | **exact match** |",
        "| tab:q0_cold | data/q0/scale16_summary.csv | **exact match** |",
        "| tab:q1_util | data/q1/sweep_summary.csv | **diverges** (see note 1) |",
        "| tab:q1_mix | data/q1/mix_summary.csv | **diverges** (see notes 1–2) |",
        "| tab:q2_phase | data/q2/main4_bw9e5_d2_summary.csv | matches except STPS row (note 3) |",
        "| tab:q2_dmax | data/q2/main4_bw9e5_d{2,4,8,16}_t400_summary.csv | **exact match** |",
        "| tab:q2_regime_* | main4_bw9e5_d16 / bw5e6_d16 / bw9e5_d16_t400 | **exact match** |",
        "",
        "**Note 1 — Q1 tables use a stale calibration in the paper.** The paper's "
        "`tab:q1_util`/`tab:q1_mix` were produced under an old `card_capacity=4500` "
        "scaling (see `docs/Q1_TODO.md`), so the absolute card-CV values differ from "
        "this fresh run on the current card model. The qualitative trends are "
        "preserved (CV falls as utilisation rises; Stage-A's advantage erodes as the "
        "bursty share grows), but the numbers should be refreshed from the CSVs here.",
        "",
        "**Note 2 — baselines are *not* constant across the fingerprint mix.** The "
        "paper claims baseline card-CV is identical at every Flat/Bursty ratio. On the "
        "current code the realised per-card *load* depends on each task's fingerprint "
        "traffic, so even fingerprint-blind baselines shift with the mix. The constant-"
        "column narrative no longer holds.",
        "",
        "**Note 3 — `tab:q2_phase` STPS row was taken at the wrong D_max.** The caption "
        "says D_max=2, but the paper's `stps-spatial+ps` congestion (0.160) is the "
        "D_max=16 value; at D_max=2 it is 0.247 — which is exactly the full-STPS "
        "congestion reported in `tab:q0_main`. The regenerated table below uses the "
        "captioned D_max=2 and is therefore internally consistent with the main table.",
        "",
    ]
    q0_main(lines)
    q0_cold(lines)
    q1_util(lines)
    q1_mix(lines)
    q2_phase(lines)
    q2_dmax(lines)
    q2_regime(lines)
    OUT.write_text("\n".join(lines) + "\n")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
