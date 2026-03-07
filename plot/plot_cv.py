"""Plot Coefficient of Variation (CV) for load distribution analysis."""
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
        description="Plot Coefficient of Variation (CV) for scheduler load distribution"
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
        help="Output image path. Default: plot/cv_comparison.png",
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
COLORS = ["#0b3c5d", "#b22222", "#2f4f4f", "#5e35b1", "#006400", "#ff8c00", "#e74c3c"]
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


def compute_cv(per_step_loads: Dict[int, List[float]]) -> Dict[int, float]:
    """
    Compute Coefficient of Variation (CV) for each time step.
    
    CV = σ / μ = sqrt(variance) / mean
    
    Args:
        per_step_loads: Dictionary mapping time_step to list of load values
        
    Returns:
        Dictionary mapping time_step to CV value
    """
    cv_by_step = {}
    
    for t in sorted(per_step_loads.keys()):
        loads = per_step_loads[t]
        if len(loads) < 2:
            cv_by_step[t] = 0.0
            continue
        
        mean_load = np.mean(loads)
        if mean_load == 0:
            cv_by_step[t] = 0.0
            continue
        
        std_load = np.std(loads)
        cv = std_load / mean_load
        cv_by_step[t] = cv
    
    return cv_by_step


def infer_label(csv_path: Path) -> str:
    """Infer a label from the CSV filename."""
    stem = csv_path.stem.lower()
    parts = stem.split("_")
    # Handle two-part prefixes like glass_drl
    if len(parts) >= 2 and parts[0] == "glass" and parts[1] == "drl":
        scheduler = "glass_drl"
    else:
        scheduler = parts[0]
    scheduler_names = {
        "glass": "Glass",
        "gandiva": "Gandiva",
        "gandivaspike": "Gandiva",
        "glass_drl": "Glass-DRL",
        "bestfit": "BestFit",
        "drf": "DRF",
        "p2c": "P2C",
        "roundrobin": "RoundRobin",
        "rr": "RoundRobin",
        "static": "Static",
    }
    return scheduler_names.get(scheduler, scheduler.upper())


def plot_cv_comparison(
    data_list: List[Tuple[str, Dict[int, float]]],
    output: Path,
) -> Path:
    """
    Plot CV comparison: GLaSS vs best among other schedulers.
    
    Args:
        data_list: List of (label, cv_by_step) tuples
        output: Output file path
        
    Returns:
        Path to saved output file
    """
    output.parent.mkdir(parents=True, exist_ok=True)
    output = output.with_suffix(".png")
    
    # Find Gandiva data and compute average CV for all schedulers
    gandiva_data = None
    scheduler_avgs = {}  # Map: label -> avg CV
    
    for label, cv_dict in data_list:
        cv_values = list(cv_dict.values())
        avg_cv = np.mean(cv_values) if cv_values else 0
        scheduler_avgs[label] = avg_cv
        
        # Accept 'gandivaspike' prefix as Gandiva identifier (labels come from filename prefixes)
        if label.lower().startswith("gandivaspike") or label.lower() == "gandiva":
            gandiva_data = (label, cv_dict)
    
    if gandiva_data is None:
        print("Error: Gandiva data not found in input.")
        return output
    
    # Find best non-Gandiva scheduler (lowest CV is best)
    other_schedulers = {k: v for k, v in scheduler_avgs.items() if k.lower() != "gandiva" and not k.lower().startswith("gandivaspike")}
    best_label = min(other_schedulers.items(), key=lambda x: x[1])[0]
    best_data = next((label, cv_dict) for label, cv_dict in data_list if label == best_label)
    
    # Collect time steps
    all_steps = set()
    all_steps.update(gandiva_data[1].keys())
    all_steps.update(best_data[1].keys())
    sorted_steps = sorted(all_steps)
    
    fig, ax = plt.subplots(figsize=(12, 6))
    
    # Plot Gandiva and best other scheduler
    comparison_data = [gandiva_data, best_data]
    colors = ["#0b3c5d", "#b22222"]  # Dark blue for Gandiva, crimson for best other
    line_styles = ["-", "--"]
    markers = ["o", "s"]
    
    for idx, (label, cv_dict) in enumerate(comparison_data):
        cv_values = [cv_dict.get(t, 0.0) for t in sorted_steps]
        
        ax.plot(
            sorted_steps,
            cv_values,
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
    ax.set_ylabel("Coefficient of Variation (CV)", fontsize=13, fontweight="bold")
    
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
        cv_by_step = compute_cv(per_step_loads)
        
        if args.labels and idx < len(args.labels):
            label = args.labels[idx]
        else:
            label = infer_label(csv_path)
        
        data_list.append((label, cv_by_step))
        
        # Print summary statistics
        cv_values = list(cv_by_step.values())
        avg_cv = np.mean(cv_values) if cv_values else 0
        max_cv = np.max(cv_values) if cv_values else 0
        min_cv = np.min(cv_values) if cv_values else 0
        print(f"{label}: Avg CV = {avg_cv:.4f}, Max CV = {max_cv:.4f}, Min CV = {min_cv:.4f}")
    
    if not data_list:
        print("No valid data to plot.")
        return
    
    # Determine output path
    if args.output is None:
        out_dir = Path("plot")
        out_dir.mkdir(parents=True, exist_ok=True)
        output = out_dir / "cv_comparison.png"
    else:
        output = args.output
    
    plot_path = plot_cv_comparison(data_list, output)
    print(f"Saved CV comparison plot to {plot_path}")


if __name__ == "__main__":
    main()
