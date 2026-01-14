"""Plot Load Imbalance Factor (LIF) for load distribution analysis."""
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
        description="Plot Load Imbalance Factor (LIF) for scheduler load distribution"
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
        help="Output image path. Default: plot/lif_comparison.png",
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


def compute_lif(per_step_loads: Dict[int, List[float]]) -> Dict[int, float]:
    """
    Compute Load Imbalance Factor (LIF) for each time step.
    
    LIF = (ρ_max / ρ_avg) - 1
    
    Where:
    - ρ_max: Maximum load across all cards
    - ρ_avg: Average load across all cards
    
    Range: [0, ∞)
    - LIF = 0: Perfect balance (all loads equal)
    - LIF > 0: Imbalance present (higher = worse)
    - LIF = 1: Max load is 2x average
    
    Args:
        per_step_loads: Dictionary mapping time_step to list of load values
        
    Returns:
        Dictionary mapping time_step to LIF value
    """
    lif_by_step = {}
    
    for t in sorted(per_step_loads.keys()):
        loads = per_step_loads[t]
        if len(loads) < 2:
            lif_by_step[t] = 0.0
            continue
        
        loads_array = np.array(loads)
        
        max_load = np.max(loads_array)
        avg_load = np.mean(loads_array)
        
        if avg_load == 0:
            lif_by_step[t] = 0.0
            continue
        
        # LIF = (ρ_max / ρ_avg) - 1
        lif = (max_load / avg_load) 
        lif_by_step[t] = lif
    
    return lif_by_step


def infer_label(csv_path: Path) -> str:
    """Infer a label from the CSV filename."""
    # Use filename prefix up to first underscore as the label
    stem = csv_path.stem
    prefix = stem.split("_")[0]
    return prefix


def plot_lif_comparison(
    data_list: List[Tuple[str, Dict[int, float]]],
    output: Path,
    scheduler_avgs: Dict[str, float],
) -> Path:
    """
    Plot LIF comparison: GLaSS vs best among other schedulers.
    
    Args:
        data_list: List of (label, lif_by_step) tuples
        output: Output file path
        scheduler_avgs: Map of label -> avg LIF
        
    Returns:
        Path to saved output file
    """
    output.parent.mkdir(parents=True, exist_ok=True)
    output = output.with_suffix(".png")
    
    # Collect time steps
    all_steps = set()
    for _, lif_dict in data_list:
        all_steps.update(lif_dict.keys())
    sorted_steps = sorted(all_steps)
    
    fig, ax = plt.subplots(figsize=(12, 6))
    
    # Plot GLaSS and best other scheduler
    colors = ["#0b3c5d", "#b22222"]  # Dark blue for GLaSS, crimson for best other
    line_styles = ["-", "--"]
    markers = ["o", "s"]
    
    for idx, (label, lif_dict) in enumerate(data_list):
        lif_values = [lif_dict.get(t, 0.0) for t in sorted_steps]
        
        ax.plot(
            sorted_steps,
            lif_values,
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
    ax.set_ylabel("Load Imbalance Factor (LIF)", fontsize=13, fontweight="bold")
    
    # Optimize X-axis
    ax.xaxis.set_major_locator(MaxNLocator(integer=True, steps=[10]))
    ax.tick_params(axis="both", labelsize=11)
    
    # Legend
    ax.legend(frameon=False, loc="upper right", fontsize=11)
    
    # Grid
    ax.grid(True, alpha=0.3, linestyle="--")
    
    # Y-axis
    ax.yaxis.set_major_locator(MaxNLocator(integer=False))
    
    fig.tight_layout()
    fig.savefig(output, dpi=150)
    plt.close(fig)
    
    return output


def main() -> None:
    args = parse_args()
    
    # Determine CSV files to process. If none provided, use all load CSVs under data/
    if not args.csv_files:
        csv_files = sorted(Path("data").glob("*_loads_*.csv"))
    else:
        csv_files = args.csv_files

    # Load data from selected CSV files
    data_list = []
    for idx, csv_path in enumerate(csv_files):
        if not csv_path.exists():
            print(f"Warning: {csv_path} not found, skipping.")
            continue
        
        per_step_loads = load_data(csv_path)
        lif_by_step = compute_lif(per_step_loads)
        
        if args.labels and idx < len(args.labels):
            label = args.labels[idx]
        else:
            label = infer_label(csv_path)
        
        data_list.append((label, lif_by_step))
        
        # Print summary statistics
        lif_values = list(lif_by_step.values())
        avg_lif = np.mean(lif_values) if lif_values else 0
        max_lif = np.max(lif_values) if lif_values else 0
        min_lif = np.min(lif_values) if lif_values else 0
        print(f"{label}: Avg LIF = {avg_lif:.4f}, Max LIF = {max_lif:.4f}, Min LIF = {min_lif:.4f}")
    
    if not data_list:
        print("No valid data to plot.")
        return
    
    # Find GLaSS data and compute average LIF for all schedulers
    glass_data = None
    scheduler_avgs = {}  # Map: label -> avg LIF
    
    for label, lif_dict in data_list:
        lif_values = list(lif_dict.values())
        avg_lif = np.mean(lif_values) if lif_values else 0
        scheduler_avgs[label] = avg_lif
        
        # Accept 'glass' prefix as GLaSS identifier (labels come from filename prefixes)
        if label.lower().startswith("glass") or label == "GLaSS":
            glass_data = (label, lif_dict)
    
    if glass_data is None:
        print("Error: GLaSS data not found in input.")
        return
    
    # Find best non-GLaSS scheduler (lowest LIF is best)
    other_schedulers = {k: v for k, v in scheduler_avgs.items() if not k.lower().startswith("glass") and k != "GLaSS"}
    best_label = min(other_schedulers.items(), key=lambda x: x[1])[0]
    best_data = next((label, lif_dict) for label, lif_dict in data_list if label == best_label)
    
    # Determine output path
    if args.output is None:
        out_dir = Path("plot")
        out_dir.mkdir(parents=True, exist_ok=True)
        output = out_dir / "lif_comparison.png"
    else:
        output = args.output
    
    plot_path = plot_lif_comparison([glass_data, best_data], output, scheduler_avgs)
    print(f"Saved LIF comparison plot to {plot_path}")


if __name__ == "__main__":
    main()
