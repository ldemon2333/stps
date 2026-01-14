"""Compare load variance between different schedulers."""
from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import MaxNLocator


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare load variance between schedulers over time"
    )
    parser.add_argument(
        "csv_files",
        type=Path,
        nargs="+",
        help="CSV files to compare (e.g., glass_loads.csv static_loads.csv)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output image path. Default: plot/compare_variance.pdf",
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
        help="Output format: pdf (default) or png",
    )
    return parser.parse_args()


# High-contrast palette
COLORS = ["#0b3c5d", "#b22222", "#2f4f4f", "#5e35b1", "#006400", "#ff8c00"]
plt.rcParams["font.family"] = ["DejaVu Sans", "DejaVu Serif", "sans-serif"]


def load_data(csv_path: Path):
    """Load CSV data and return per-card times and loads."""
    per_card_times: dict[int, list[int]] = defaultdict(list)
    per_card_loads: dict[int, list[float]] = defaultdict(list)

    with csv_path.open() as f:
        reader = csv.DictReader(f)
        for row in reader:
            t = int(row["time_step"])
            card_id = int(row["card_id"])
            load = float(row["load"])
            per_card_times[card_id].append(t)
            per_card_loads[card_id].append(load)

    return per_card_times, per_card_loads


def compute_variance(per_card_times, per_card_loads):
    """Compute load variance across cards for each time step."""
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


def write_variance_csv(data_list: list[tuple[str, dict]], output_dir: Path) -> Path:
    """Write variance data to CSV file for both schedulers."""
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = output_dir / f"variance_comparison_{timestamp}.csv"
    
    # Collect all time steps
    all_times = set()
    for _, variance_by_time in data_list:
        all_times.update(variance_by_time.keys())
    
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
            for _, variance_by_time in data_list:
                variance = variance_by_time.get(t, 0.0)
                row.append(variance)
            writer.writerow(row)
    
    return csv_path


def infer_label(csv_path: Path) -> str:
    """Infer a label from the CSV filename."""
    name = csv_path.stem.lower()
    
    # Extract scheduler name (first part before _)
    parts = name.split("_")
    scheduler = parts[0] if parts else name
    
    # Map to readable names
    scheduler_names = {
        "glass": "GLaSS",
        "bestfit": "BestFit",
        "drf": "DRF",
        "p2c": "P2C",
        "static": "Static",
    }
    
    label = scheduler_names.get(scheduler, scheduler.upper())
    
    # Check for arrival mode in filename
    for mode in ["poisson", "bursty", "mixed"]:
        if mode in name:
            label = f"{label} ({mode})"
            break
    
    return label


def plot_comparison(data_list: list[tuple[str, dict]], output: Path, format: str = "pdf"):
    """Plot variance comparison for multiple schedulers."""
    output.parent.mkdir(parents=True, exist_ok=True)
    
    # Ensure output has correct extension
    output = output.with_suffix(f".{format}")
    
    fig, axes = plt.subplots(2, 1, figsize=(12, 8), height_ratios=[3, 1.5])
    ax_main = axes[0]
    ax_diff = axes[1]
    
    # Store data for difference calculation
    all_variances = {}
    
    for idx, (label, variance_by_time) in enumerate(data_list):
        times = sorted(variance_by_time.keys())
        variances = [variance_by_time[t] for t in times]
        avg_var = sum(variances) / len(variances) if variances else 0
        
        color = COLORS[idx % len(COLORS)]
        ax_main.plot(
            times, variances, 
            color=color, 
            linewidth=1.5, 
            label=f"{label} (Avg: {avg_var:.2f})",
            alpha=0.9
        )
        ax_main.fill_between(times, variances, alpha=0.15, color=color)
        
        all_variances[label] = (times, variances, variance_by_time)
    
    ax_main.set_ylabel("Load Variance")
    ax_main.set_xlabel("")
    ax_main.grid(True, alpha=0.3)
    ax_main.xaxis.set_major_locator(MaxNLocator(integer=True))
    ax_main.legend(frameon=False, loc="upper right")
    ax_main.set_title("Load Variance Comparison per Time Step")
    
    # Plot difference if we have exactly 2 schedulers
    if len(data_list) == 2:
        label1, var1 = data_list[0][0], data_list[0][1]
        label2, var2 = data_list[1][0], data_list[1][1]
        
        common_times = sorted(set(var1.keys()) & set(var2.keys()))
        diffs = [var2[t] - var1[t] for t in common_times]  # Static - GLaSS
        
        # Color by sign: green when GLaSS is better (positive diff), red when Static is better
        colors = ["#006400" if d > 0 else "#b22222" for d in diffs]
        ax_diff.bar(common_times, diffs, color=colors, alpha=0.7, width=0.8)
        ax_diff.axhline(0, color="#555", linestyle="-", linewidth=1.0)
        
        avg_diff = sum(diffs) / len(diffs) if diffs else 0
        ax_diff.axhline(avg_diff, color="#ff8c00", linestyle="--", linewidth=1.5, 
                       label=f"Avg Diff: {avg_diff:.2f}")
        
        ax_diff.set_ylabel(f"Δ Variance\n({label2} - {label1})")
        ax_diff.set_xlabel("Time Step")
        ax_diff.grid(True, alpha=0.3)
        ax_diff.xaxis.set_major_locator(MaxNLocator(integer=True))
        ax_diff.legend(frameon=False, loc="upper right")
        
        # Add annotation
        positive_count = sum(1 for d in diffs if d > 0)
        ax_diff.set_title(
            f"Variance Difference (Green: {label1} better | {positive_count}/{len(diffs)} steps)"
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
        variance_by_time = compute_variance(per_card_times, per_card_loads)
        
        if args.labels and idx < len(args.labels):
            label = args.labels[idx]
        else:
            label = infer_label(csv_path)
        
        data_list.append((label, variance_by_time))
        
        # Print summary
        variances = list(variance_by_time.values())
        avg_var = sum(variances) / len(variances) if variances else 0
        max_var = max(variances) if variances else 0
        min_var = min(variances) if variances else 0
        print(f"{label}: Avg Variance = {avg_var:.2f}, Max Variance = {max_var:.2f}, Min Variance = {min_var:.2f}")
    
    if not data_list:
        print("No valid data to plot.")
        return
    
    # Determine output path
    if args.output is None:
        out_dir = Path("plot")
        out_dir.mkdir(parents=True, exist_ok=True)
        output = out_dir / f"compare_variance.{args.format}"
    else:
        output = args.output
    
    # Write variance data to CSV
    csv_path = write_variance_csv(data_list, Path("data"))
    print(f"Saved variance data to {csv_path}")
    
    plot_path = plot_comparison(data_list, output, format=args.format)
    print(f"Saved comparison plot to {plot_path}")


if __name__ == "__main__":
    main()
