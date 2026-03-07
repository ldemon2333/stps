"""Compare Coefficient of Variation (CV) between different schedulers."""
from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.ticker import MaxNLocator


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare load Coefficient of Variation (CV) between schedulers over time"
    )
    parser.add_argument(
        "csv_files",
        type=Path,
        nargs="+",
        help="CSV files to compare (e.g., glass_loads.csv gandiva_loads.csv)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output image path. Default: plot/compare_cv.png",
    )
    parser.add_argument(
        "--labels",
        type=str,
        nargs="+",
        default=None,
        help="Labels for each CSV file. If omitted, inferred from filename.",
    )
    parser.add_argument(
        "--format",
        type=str,
        default="png",
        choices=["pdf", "png"],
        help="Output format: png (default) or pdf",
    )
    return parser.parse_args()


# High-contrast palette
COLORS = ["#0b3c5d", "#b22222", "#2f4f4f", "#5e35b1", "#006400", "#ff8c00", "#e74c3c"]
plt.rcParams["font.family"] = ["DejaVu Sans", "DejaVu Serif", "sans-serif"]


def load_data(csv_path: Path):
    """Load CSV data and return per-card times and loads."""
    per_card_times: dict[int, list[int]] = defaultdict(list)
    per_card_loads: dict[int, list[float]] = defaultdict(list)

    with csv_path.open() as f:
        reader = csv.DictReader(f)
        # Skip CSVs without time_step column (e.g., summary files)
        if not reader.fieldnames or "time_step" not in reader.fieldnames:
            print(f"Warning: {csv_path} does not contain 'time_step' column; skipping file.")
            return per_card_times, per_card_loads
        
        for row in reader:
            t = int(row["time_step"])
            card_id = int(row["card_id"])
            load = float(row["load"])
            per_card_times[card_id].append(t)
            per_card_loads[card_id].append(load)

    return per_card_times, per_card_loads


def compute_cv(per_card_times, per_card_loads):
    """
    Compute Coefficient of Variation (CV) across cards for each time step.
    
    CV = σ / μ = sqrt(variance) / mean
    
    Returns:
        Dictionary mapping time_step to CV value
    """
    times = sorted({t for ts in per_card_times.values() for t in ts})
    cv_by_time = {}
    
    for t in times:
        vals = []
        for cid, loads in per_card_loads.items():
            time_map = dict(zip(per_card_times[cid], loads))
            if t in time_map:
                vals.append(time_map[t])
        
        if len(vals) >= 2:
            mean = np.mean(vals)
            if mean == 0:
                cv_by_time[t] = 0.0
            else:
                std = np.std(vals)
                cv = std / mean
                cv_by_time[t] = cv
        elif vals:
            cv_by_time[t] = 0.0
    
    return cv_by_time


def write_cv_csv(data_list: list[tuple[str, dict]], output_dir: Path) -> Path:
    """Write CV data to CSV file for all schedulers."""
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = output_dir / f"cv_comparison_{timestamp}.csv"
    
    # Collect all time steps
    all_times = set()
    for _, cv_by_time in data_list:
        all_times.update(cv_by_time.keys())
    
    sorted_times = sorted(all_times)
    
    # Write CSV
    with csv_path.open("w", newline="") as f:
        writer = csv.writer(f)
        
        # Header
        header = ["time_step"] + [label for label, _ in data_list]
        writer.writerow(header)
        
        # Data rows
        for t in sorted_times:
            row = [t]
            for _, cv_by_time in data_list:
                cv = cv_by_time.get(t, 0.0)
                row.append(cv)
            writer.writerow(row)
    
    return csv_path


def infer_label(csv_path: Path) -> str:
    """Infer a label from the CSV filename."""
    name = csv_path.stem.lower()
    
    # Extract scheduler name (first part before _)
    parts = name.split("_")
    # Handle two-part prefixes like glass_drl
    if len(parts) >= 2 and parts[0] == "glass" and parts[1] == "drl":
        scheduler = "glass_drl"
    else:
        scheduler = parts[0] if parts else name
    
    # Map to readable names
    scheduler_names = {
        "glass": "Glass",
        "gandiva": "Gandiva",
        "gandivaspike": "Gandiva",
        "glass-drl": "Glass-DRL",
        "glass_drl": "Glass-DRL",
        "bestfit": "BestFit",
        "drf": "DRF",
        "p2c": "P2C",
        "roundrobin": "RoundRobin",
        "rr": "RoundRobin",
        "static": "Static",
        "gg": "Glass",
    }
    
    label = scheduler_names.get(scheduler, scheduler.upper())
    
    # Check for arrival mode in filename
    for mode in ["poisson", "bursty", "mixed"]:
        if mode in name:
            label = f"{label} ({mode})"
            break
    
    return label


def plot_comparison(data_list: list[tuple[str, dict]], output: Path, format: str = "png"):
    """Plot CV comparison for multiple schedulers."""
    output.parent.mkdir(parents=True, exist_ok=True)
    
    # Ensure output has correct extension
    output = output.with_suffix(f".{format}")
    
    fig, axes = plt.subplots(2, 1, figsize=(12, 8), height_ratios=[3, 1.5])
    ax_main = axes[0]
    ax_diff = axes[1]
    
    # Store data for difference calculation
    all_cvs = {}
    
    for idx, (label, cv_by_time) in enumerate(data_list):
        times = sorted(cv_by_time.keys())
        cvs = [cv_by_time[t] for t in times]
        avg_cv = sum(cvs) / len(cvs) if cvs else 0
        
        color = COLORS[idx % len(COLORS)]
        ax_main.plot(
            times, cvs, 
            color=color, 
            linewidth=1.5, 
            label=f"{label} (Avg: {avg_cv:.4f})",
            alpha=0.9
        )
        ax_main.fill_between(times, cvs, alpha=0.15, color=color)
        
        all_cvs[label] = (times, cvs, cv_by_time)
    
    ax_main.set_ylabel("Coefficient of Variation (CV)", fontweight="bold")
    ax_main.set_xlabel("")
    ax_main.grid(True, alpha=0.3)
    ax_main.xaxis.set_major_locator(MaxNLocator(integer=True))
    ax_main.legend(frameon=False, loc="upper right")
    ax_main.set_title("Coefficient of Variation Comparison per Time Step", fontweight="bold")
    
    # Plot difference if we have exactly 2 schedulers
    if len(data_list) == 2:
        label1, cv1 = data_list[0][0], data_list[0][1]
        label2, cv2 = data_list[1][0], data_list[1][1]
        
        common_times = sorted(set(cv1.keys()) & set(cv2.keys()))
        diffs = [cv2[t] - cv1[t] for t in common_times]  # Scheduler2 - Scheduler1
        
        # Color by sign: green when first scheduler is better (positive diff means second is worse)
        # Red when second scheduler is better (negative diff means second is better)
        colors = ["#b22222" if d > 0 else "#006400" for d in diffs]
        ax_diff.bar(common_times, diffs, color=colors, alpha=0.7, width=0.8)
        ax_diff.axhline(0, color="#555", linestyle="-", linewidth=1.0)
        
        avg_diff = sum(diffs) / len(diffs) if diffs else 0
        ax_diff.axhline(avg_diff, color="#ff8c00", linestyle="--", linewidth=1.5, 
                       label=f"Avg Diff: {avg_diff:.4f}")
        
        ax_diff.set_ylabel(f"Δ CV\n({label2} - {label1})", fontweight="bold")
        ax_diff.set_xlabel("Time Step", fontweight="bold")
        ax_diff.grid(True, alpha=0.3)
        ax_diff.xaxis.set_major_locator(MaxNLocator(integer=True))
        ax_diff.legend(frameon=False, loc="upper right")
        
        # Add annotation (green means first scheduler is better = lower CV)
        better_count = sum(1 for d in diffs if d > 0)
        ax_diff.set_title(
            f"CV Difference ({label1} better | {better_count}/{len(diffs)} steps)",
            fontweight="bold"
        )
    else:
        ax_diff.set_visible(False)
    
    fig.tight_layout()
    dpi = 150 if format == "png" else 100
    fig.savefig(output, dpi=dpi)
    plt.close(fig)
    
    return output


def main() -> None:
    args = parse_args()
    
    # Load data from all CSV files
    data_list = []
    for idx, csv_path in enumerate(args.csv_files):
        if not csv_path.exists():
            print(f"Warning: {csv_path} not found, skipping.")
            continue
        
        per_card_times, per_card_loads = load_data(csv_path)
        
        # Skip if no data loaded (e.g., summary CSV)
        if not per_card_times:
            continue
        
        cv_by_time = compute_cv(per_card_times, per_card_loads)
        
        if args.labels and idx < len(args.labels):
            label = args.labels[idx]
        else:
            label = infer_label(csv_path)
        
        data_list.append((label, cv_by_time))
        
        # Print summary
        cvs = list(cv_by_time.values())
        avg_cv = sum(cvs) / len(cvs) if cvs else 0
        max_cv = max(cvs) if cvs else 0
        min_cv = min(cvs) if cvs else 0
        print(f"{label}: Avg CV = {avg_cv:.4f}, Max CV = {max_cv:.4f}, Min CV = {min_cv:.4f}")
    
    if not data_list:
        print("No valid data to plot.")
        return
    
    # Determine output path
    if args.output is None:
        out_dir = Path("plot")
        out_dir.mkdir(parents=True, exist_ok=True)
        output = out_dir / f"compare_cv.{args.format}"
    else:
        output = args.output
    
    # Write CV data to CSV
    csv_path = write_cv_csv(data_list, Path("data"))
    print(f"Saved CV data to {csv_path}")
    
    plot_path = plot_comparison(data_list, output, format=args.format)
    print(f"Saved CV comparison plot to {plot_path}")


if __name__ == "__main__":
    main()
