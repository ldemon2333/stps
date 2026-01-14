"""Generate variance summary table for all CSVs in data/.

Produces:
- data/variance_summary_<timestamp>.csv (time series stats)
- plot/variance_summary_<timestamp>.png (table image)

Usage:
    python plot/plot_variance_summary.py
"""
from __future__ import annotations

import csv
from collections import defaultdict
from datetime import datetime
from pathlib import Path
import math

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

SCRIPT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = (SCRIPT_ROOT / "data").resolve()
OUT_DIR = (SCRIPT_ROOT / "plot").resolve()


def load_data(csv_path: Path):
    per_card_times: dict[int, list[int]] = defaultdict(list)
    per_card_loads: dict[int, list[float]] = defaultdict(list)

    with csv_path.open() as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                t = int(row["time_step"])
                card_id = int(row["card_id"])
                load = float(row["load"])
            except Exception:
                continue
            per_card_times[card_id].append(t)
            per_card_loads[card_id].append(load)

    return per_card_times, per_card_loads


def compute_variance(per_card_times, per_card_loads):
    times = sorted({t for ts in per_card_times.values() for t in ts})
    variance_by_time = {}

    for t in times:
        vals = []
        for cid, loads in per_card_loads.items():
            time_map = dict(zip(per_card_times[cid], loads))
            if t in time_map:
                vals.append(time_map[t])
        if len(vals) >= 2:
            mean = sum(vals) / len(vals)
            variance = sum((v - mean) ** 2 for v in vals) / len(vals)
            variance_by_time[t] = variance
        elif vals:
            variance_by_time[t] = 0.0

    return variance_by_time


def summarize_variance(variance_by_time: dict[int, float]):
    if not variance_by_time:
        return (0.0, 0.0, 0.0)
    vals = list(variance_by_time.values())
    avg = sum(vals) / len(vals)
    mx = max(vals)
    mn = min(vals)
    return (avg, mx, mn)


def main():
    files = sorted(DATA_DIR.glob("*.csv"))
    if not files:
        print(f"No CSV files found in {DATA_DIR}")
        return

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    summary_csv = DATA_DIR / f"variance_summary_{timestamp}.csv"
    img_path = OUT_DIR / f"variance_summary_{timestamp}.png"

    rows = []

    for f in files:
        per_card_times, per_card_loads = load_data(f)
        variance_by_time = compute_variance(per_card_times, per_card_loads)
        avg, mx, mn = summarize_variance(variance_by_time)
        label = f.stem
        rows.append((label, avg, mx, mn))
        print(f"{label}: Avg={avg:.4f}, Max={mx:.4f}, Min={mn:.4f}")

    # Write CSV
    with summary_csv.open("w", newline="") as out:
        writer = csv.writer(out)
        writer.writerow(["algorithm", "avg_variance", "max_variance", "min_variance"])
        for r in rows:
            writer.writerow([r[0], f"{r[1]:.6f}", f"{r[2]:.6f}", f"{r[3]:.6f}"])

    # Create table image
    col_labels = ["algorithm", "Avg Variance", "Max Variance", "Min Variance"]
    cell_text = []
    for r in rows:
        cell_text.append([r[0], f"{r[1]:.2f}", f"{r[2]:.4f}", f"{r[3]:.4f}"])

    fig, ax = plt.subplots(figsize=(8, 2 + 0.4 * len(cell_text)))
    ax.axis('off')
    table = ax.table(cellText=cell_text, colLabels=col_labels, loc='center')
    table.auto_set_font_size(False)
    table.set_fontsize(10)
    table.scale(1, 1.2)

    plt.title('Variance Summary')
    plt.tight_layout()
    fig.savefig(img_path, dpi=150)
    plt.close(fig)

    print(f"Saved summary CSV to {summary_csv}")
    print(f"Saved summary image to {img_path}")


if __name__ == '__main__':
    main()
