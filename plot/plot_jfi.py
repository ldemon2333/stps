"""Plot Jain's Fairness Index (JFI) for load distribution analysis."""
from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Tuple

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.ticker import MaxNLocator


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Plot Jain's Fairness Index (JFI) for scheduler load distribution"
    )
    parser.add_argument(
        "csv_files",
        type=Path,
        nargs="*",
        help="CSV files with load data (e.g., glass_loads.csv drf_loads.csv). If omitted, all CSVs under data/ are used.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output image path. Default: plot/jfi_comparison.png",
    )
    parser.add_argument(
        "--labels",
        type=str,
        nargs="+",
        default=None,
        help="Labels for each CSV file. If omitted, inferred from filename.",
    )
    return parser.parse_args()


# High-contrast palette
COLORS = ["#0b3c5d", "#b22222", "#2f4f4f", "#5e35b1", "#006400", "#ff8c00"]
plt.rcParams["font.family"] = ["DejaVu Sans", "DejaVu Serif", "sans-serif"]


def load_data(csv_path: Path) -> Dict[int, List[float]]:
    """
    Load CSV data and organize by time step.
    
    Args:
        csv_path: Path to CSV file with columns: time_step, card_id, load
        
    Returns:
        Dictionary mapping time_step to list of load values
    """
    per_step_loads: Dict[int, List[float]] = defaultdict(list)
    
    with csv_path.open() as f:
        reader = csv.DictReader(f)
         # If this CSV does not contain per-step load rows (e.g., summary CSVs), skip it
        if not reader.fieldnames or "time_step" not in reader.fieldnames:
            print(f"Warning: {csv_path} does not contain 'time_step' column; skipping file.")
            return per_step_loads
        for row in reader:
            t = int(row["time_step"])
            load = float(row["load"])
            per_step_loads[t].append(load)
    
    return per_step_loads


def compute_jfi(per_step_loads: Dict[int, List[float]]) -> Dict[int, float]:
    """
    Compute Jain's Fairness Index (JFI) for each time step.
    
    JFI = (Σ L_m)^2 / (M * Σ L_m^2)
    
    Range: [1/M, 1]
    - JFI = 1: Perfect fairness (all loads equal)
    - JFI = 1/M: Worst fairness (one card has all load)
    - JFI > 0.8: Generally considered good fairness
    
    Args:
        per_step_loads: Dictionary mapping time_step to list of load values
        
    Returns:
        Dictionary mapping time_step to JFI value
    """
    jfi_by_step = {}
    
    for t in sorted(per_step_loads.keys()):
        loads = per_step_loads[t]
        if len(loads) < 2:
            jfi_by_step[t] = 1.0  # Perfect fairness if only one or zero cards
            continue
        
        loads_array = np.array(loads)
        M = len(loads)
        
        sum_loads = np.sum(loads_array)
        if sum_loads == 0:
            jfi_by_step[t] = 1.0
            continue
        
        sum_loads_sq = np.sum(loads_array ** 2)
        
        # JFI = (Σ L_m)^2 / (M * Σ L_m^2)
        jfi = (sum_loads ** 2) / (M * sum_loads_sq)
        jfi_by_step[t] = jfi
    
    return jfi_by_step


def infer_label(csv_path: Path) -> str:
    """Return filename prefix before the first underscore as label."""
    stem = csv_path.stem
    prefix = stem.split("_")[0]
    return prefix


def plot_jfi_comparison(
    data_list: List[Tuple[str, Dict[int, float]]],
    output: Path,
) -> Path:
    """
    Plot JFI comparison: GLaSS vs best among other schedulers.
    
    Args:
        data_list: List of (label, jfi_by_step) tuples
        output: Output file path
        
    Returns:
        Path to saved output file
    """
    output.parent.mkdir(parents=True, exist_ok=True)
    output = output.with_suffix(".png")
    
    # Find GLaSS data and compute average JFI for all schedulers
    glass_data = None
    scheduler_avgs = {}  # Map: label -> avg JFI

    for label, jfi_dict in data_list:
        jfi_values = list(jfi_dict.values())
        avg_jfi = np.mean(jfi_values) if jfi_values else 0
        scheduler_avgs[label] = avg_jfi

        # Accept labels starting with 'glass' (from filename prefix) as GLaSS
        if label.lower().startswith("glass") or label == "GLaSS":
            glass_data = (label, jfi_dict)
    
    if glass_data is None:
        print("Error: GLaSS data not found in input.")
        return output
    
    # Find best non-GLaSS scheduler
    other_schedulers = {k: v for k, v in scheduler_avgs.items() if k != "GLaSS"}
    best_label = max(other_schedulers.items(), key=lambda x: x[1])[0]
    best_data = next((label, jfi_dict) for label, jfi_dict in data_list if label == best_label)
    
    # Collect time steps
    all_steps = set()
    all_steps.update(glass_data[1].keys())
    all_steps.update(best_data[1].keys())
    sorted_steps = sorted(all_steps)
    
    fig, ax = plt.subplots(figsize=(12, 6))
    
    # Plot GLaSS and best other scheduler
    comparison_data = [glass_data, best_data]
    colors = ["#0b3c5d", "#b22222"]  # Dark blue for GLaSS, crimson for best other
    line_styles = ["-", "--"]
    markers = ["o", "s"]
    
    for idx, (label, jfi_dict) in enumerate(comparison_data):
        jfi_values = [jfi_dict.get(t, 1.0) for t in sorted_steps]
        
        ax.plot(
            sorted_steps,
            jfi_values,
            color=colors[idx],
            linewidth=2.5,
            linestyle=line_styles[idx],
            marker=markers[idx],
            markersize=7,
            markevery=5,
            label=f"{label} (Avg: {scheduler_avgs[label]:.4f})",
            alpha=0.85,
        )
    
    ax.set_xlabel("Time Step", fontsize=13, fontweight="bold")
    ax.set_ylabel("Jain's Fairness Index (JFI)", fontsize=13, fontweight="bold")
    
    # Optimize X-axis
    ax.xaxis.set_major_locator(MaxNLocator(integer=True, steps=[10]))
    ax.tick_params(axis="both", labelsize=11)
    
    # Legend
    ax.legend(frameon=False, loc="lower right", fontsize=11)
    
    # Grid
    ax.grid(True, alpha=0.3, linestyle="--")
    
    # Y-axis: JFI ranges from 0 to 1
    ax.set_ylim(bottom=0, top=1.05)
    ax.yaxis.set_major_locator(MaxNLocator(integer=False, nbins=6))
    
    # Reference line at JFI=0.8
    ax.axhline(y=0.8, color="gray", linestyle=":", linewidth=1.5, alpha=0.5)
    
    fig.tight_layout()
    fig.savefig(output, dpi=150)
    plt.close(fig)
    
    return output


def main() -> None:
    args = parse_args()
    # If no CSVs provided, use all load CSVs under data/
    if not args.csv_files:
        csv_files = sorted(Path("data").glob("*_loads_*.csv"))
    else:
        csv_files = args.csv_files

    data_list = []
    for idx, csv_path in enumerate(csv_files):
        if not csv_path.exists():
            print(f"Warning: {csv_path} not found, skipping.")
            continue

        per_step_loads = load_data(csv_path)
        jfi_by_step = compute_jfi(per_step_loads)

        if args.labels and idx < len(args.labels):
            label = args.labels[idx]
        else:
            label = infer_label(csv_path)

        data_list.append((label, jfi_by_step))

        # Print summary statistics
        jfi_values = list(jfi_by_step.values())
        avg_jfi = np.mean(jfi_values) if jfi_values else 0
        min_jfi = np.min(jfi_values) if jfi_values else 0
        max_jfi = np.max(jfi_values) if jfi_values else 0
        print(f"{label}: Avg JFI = {avg_jfi:.4f}, Min JFI = {min_jfi:.4f}, Max JFI = {max_jfi:.4f}")
    
    if not data_list:
        print("No valid data to plot.")
        return
    
    # Determine output path
    if args.output is None:
        out_dir = Path("plot")
        out_dir.mkdir(parents=True, exist_ok=True)
        output = out_dir / "jfi_comparison.png"
    else:
        output = args.output
    
    plot_path = plot_jfi_comparison(data_list, output)
    print(f"Saved JFI comparison plot to {plot_path}")


if __name__ == "__main__":
    main()
