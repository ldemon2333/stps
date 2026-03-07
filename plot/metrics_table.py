"""Generate metrics summary table from experiment results."""
from __future__ import annotations

import argparse
import re
from collections import defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate metrics summary table from experiment results"
    )
    parser.add_argument(
        "summary_file",
        type=Path,
        nargs="?",
        default=None,
        help="Metrics summary file. If omitted, uses latest in results/",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("plot/metrics.png"),
        help="Output image path. Default: plot/metrics.png",
    )
    return parser.parse_args()


# Algorithm name mapping for consistent display
ALGO_NAMES = {
    "bestfit": "BestFit",
    "drf": "DRF",
    "gandiva": "Gandiva",
    "gandivaspike": "Gandiva",  # legacy name
    "glass": "Glass",
    "glass-drl": "Glass-DRL",
    "glass_drl": "Glass-DRL",
    "p2c": "P2C",
    "roundrobin": "RR",
}

# Display order
ALGO_ORDER = ["Gandiva", "Glass", "Glass-DRL", "BestFit", "DRF", "P2C", "RR"]


def parse_summary_file(filepath: Path) -> dict:
    """
    Parse metrics summary file and extract data.
    
    Returns:
        Dict with structure: {seed: {metric: {algo: {avg, max, min}}}}
    """
    data = defaultdict(lambda: defaultdict(lambda: defaultdict(dict)))
    
    content = filepath.read_text()
    
    # Split by seed sections
    seed_pattern = r"Seed: (\d+) -"
    metric_patterns = {
        "CV": r"([\w-]+): Avg CV = ([\d.]+), Max CV = ([\d.]+), Min CV = ([\d.]+)",
        "JFI": r"([\w-]+): Avg JFI = ([\d.]+), Min JFI = ([\d.]+), Max JFI = ([\d.]+)",
        "LIF": r"([\w-]+): Avg LIF = ([\d.]+), Max LIF = ([\d.]+), Min LIF = ([\d.]+)",
    }
    
    current_seed = None
    current_metric = None
    
    for line in content.split("\n"):
        # Check for seed header
        seed_match = re.search(seed_pattern, line)
        if seed_match:
            current_seed = int(seed_match.group(1))
            continue
        
        # Check for metric section
        if "=== Coefficient of Variation (CV) ===" in line:
            current_metric = "CV"
            continue
        elif "=== Jain's Fairness Index (JFI) ===" in line:
            current_metric = "JFI"
            continue
        elif "=== Load Imbalance Factor (LIF) ===" in line:
            current_metric = "LIF"
            continue
        elif "=== CV Comparison ===" in line:
            current_metric = None  # Skip CV Comparison section (duplicate)
            continue
        
        # Parse metric data
        if current_seed is not None and current_metric is not None:
            pattern = metric_patterns.get(current_metric)
            if pattern:
                match = re.search(pattern, line)
                if match:
                    algo_raw = match.group(1).lower()
                    algo = ALGO_NAMES.get(algo_raw, algo_raw.upper())
                    
                    if current_metric == "JFI":
                        # JFI format: Avg, Min, Max
                        avg_val = float(match.group(2))
                        min_val = float(match.group(3))
                        max_val = float(match.group(4))
                    else:
                        # CV/LIF format: Avg, Max, Min
                        avg_val = float(match.group(2))
                        max_val = float(match.group(3))
                        min_val = float(match.group(4))
                    
                    data[current_seed][current_metric][algo] = {
                        "avg": avg_val,
                        "max": max_val,
                        "min": min_val,
                    }
    
    return dict(data)


def compute_aggregated_stats(data: dict) -> dict:
    """
    Compute aggregated statistics across all seeds.
    
    Returns:
        Dict with structure: {metric: {algo: {mean_avg, std_avg, mean_max, mean_min}}}
    """
    aggregated = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    
    # Collect values across seeds
    for seed, metrics in data.items():
        for metric, algos in metrics.items():
            for algo, vals in algos.items():
                aggregated[metric][algo]["avg"].append(vals["avg"])
                aggregated[metric][algo]["max"].append(vals["max"])
                aggregated[metric][algo]["min"].append(vals["min"])
    
    # Compute mean and std
    result = {}
    for metric, algos in aggregated.items():
        result[metric] = {}
        for algo, vals in algos.items():
            result[metric][algo] = {
                "mean_avg": np.mean(vals["avg"]),
                "std_avg": np.std(vals["avg"]),
                "mean_max": np.mean(vals["max"]),
                "mean_min": np.mean(vals["min"]),
            }
    
    return result


def create_table_figure(data: dict, aggregated: dict, output: Path) -> Path:
    """Create a comprehensive table figure with all metrics."""
    output.parent.mkdir(parents=True, exist_ok=True)
    
    seeds = sorted(data.keys())
    metrics = ["CV", "JFI", "LIF"]
    
    # Filter and order algorithms
    algos = [a for a in ALGO_ORDER if a in aggregated.get("CV", {})]
    
    # Create figure with subplots for each metric
    fig, axes = plt.subplots(3, 1, figsize=(14, 12))
    fig.suptitle("Scheduler Performance Metrics Summary", fontsize=16, fontweight="bold", y=0.98)
    
    metric_labels = {
        "CV": "Coefficient of Variation (CV) - Lower is Better",
        "JFI": "Jain's Fairness Index (JFI) - Higher is Better",
        "LIF": "Load Imbalance Factor (LIF) - Lower is Better",
    }
    
    for ax_idx, metric in enumerate(metrics):
        ax = axes[ax_idx]
        ax.set_title(metric_labels[metric], fontsize=12, fontweight="bold", pad=10)
        
        # Build table data
        # Columns: Algorithm | Seed1 Avg | Seed2 Avg | ... | Mean±Std | Best
        col_labels = ["Algorithm"] + [f"Seed {s}" for s in seeds] + ["Mean ± Std", "Rank"]
        
        table_data = []
        algo_means = {}
        
        for algo in algos:
            row = [algo]
            for seed in seeds:
                val = data.get(seed, {}).get(metric, {}).get(algo, {}).get("avg", 0)
                row.append(f"{val:.4f}")
            
            # Aggregated stats
            stats = aggregated.get(metric, {}).get(algo, {})
            mean_avg = stats.get("mean_avg", 0)
            std_avg = stats.get("std_avg", 0)
            row.append(f"{mean_avg:.4f} ± {std_avg:.4f}")
            algo_means[algo] = mean_avg
            
            row.append("")  # Placeholder for rank
            table_data.append(row)
        
        # Determine rankings (CV/LIF: lower is better, JFI: higher is better)
        if metric == "JFI":
            sorted_algos = sorted(algo_means.items(), key=lambda x: -x[1])
        else:
            sorted_algos = sorted(algo_means.items(), key=lambda x: x[1])
        
        rank_map = {algo: rank + 1 for rank, (algo, _) in enumerate(sorted_algos)}
        
        for row in table_data:
            algo = row[0]
            row[-1] = str(rank_map[algo])
        
        # Create table
        ax.axis("off")
        table = ax.table(
            cellText=table_data,
            colLabels=col_labels,
            loc="center",
            cellLoc="center",
        )
        
        # Style table
        table.auto_set_font_size(False)
        table.set_fontsize(10)
        table.scale(1.0, 1.8)
        
        # Color header
        for j in range(len(col_labels)):
            table[(0, j)].set_facecolor("#4472C4")
            table[(0, j)].set_text_props(color="white", fontweight="bold")
        
        # Color algorithm column
        for i in range(1, len(table_data) + 1):
            table[(i, 0)].set_facecolor("#D9E2F3")
            table[(i, 0)].set_text_props(fontweight="bold")
        
        # Highlight best performer (rank 1)
        for i, row in enumerate(table_data, 1):
            if row[-1] == "1":
                for j in range(len(col_labels)):
                    if j > 0:  # Skip algorithm name column
                        table[(i, j)].set_facecolor("#C6EFCE")
            elif row[-1] == "2":
                for j in range(len(col_labels)):
                    if j > 0:
                        table[(i, j)].set_facecolor("#FFEB9C")
    
    plt.tight_layout(rect=[0, 0, 1, 0.96])
    fig.savefig(output, dpi=150, bbox_inches="tight")
    plt.close(fig)
    
    return output


def print_summary(aggregated: dict):
    """Print text summary of results."""
    print("\n" + "=" * 70)
    print("AGGREGATED METRICS SUMMARY (Mean ± Std across all seeds)")
    print("=" * 70)
    
    metrics = ["CV", "JFI", "LIF"]
    metric_desc = {
        "CV": "Coefficient of Variation (lower is better)",
        "JFI": "Jain's Fairness Index (higher is better)",
        "LIF": "Load Imbalance Factor (lower is better)",
    }
    
    for metric in metrics:
        print(f"\n{metric}: {metric_desc[metric]}")
        print("-" * 50)
        
        algos = aggregated.get(metric, {})
        
        # Sort by performance
        if metric == "JFI":
            sorted_algos = sorted(algos.items(), key=lambda x: -x[1]["mean_avg"])
        else:
            sorted_algos = sorted(algos.items(), key=lambda x: x[1]["mean_avg"])
        
        for rank, (algo, stats) in enumerate(sorted_algos, 1):
            mean_avg = stats["mean_avg"]
            std_avg = stats["std_avg"]
            print(f"  {rank}. {algo:10s}: {mean_avg:.4f} ± {std_avg:.4f}")
    
    print("\n" + "=" * 70)


def main() -> None:
    args = parse_args()
    
    # Find summary file
    if args.summary_file is None:
        results_dir = Path("results")
        summary_files = sorted(results_dir.glob("metrics_summary_*.txt"))
        if not summary_files:
            print("Error: No metrics summary files found in results/")
            return
        summary_file = summary_files[-1]  # Use latest
        print(f"Using latest summary file: {summary_file}")
    else:
        summary_file = args.summary_file
    
    if not summary_file.exists():
        print(f"Error: {summary_file} not found")
        return
    
    # Parse data
    data = parse_summary_file(summary_file)
    print(f"Parsed data for {len(data)} seeds: {sorted(data.keys())}")
    
    # Compute aggregated statistics
    aggregated = compute_aggregated_stats(data)
    
    # Print text summary
    print_summary(aggregated)
    
    # Create table figure
    output = create_table_figure(data, aggregated, args.output)
    print(f"\nSaved metrics table to {output}")


if __name__ == "__main__":
    main()
