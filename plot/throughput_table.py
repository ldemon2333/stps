"""Generate throughput and migration summary table from multiple seeds."""
from __future__ import annotations

import argparse
import csv
import re
from collections import defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate throughput summary table from experiment results"
    )
    parser.add_argument(
        "summary_file",
        type=Path,
        nargs="?",
        default=None,
        help="Throughput summary file. If omitted, uses latest in results/",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("plot/throughput_metrics.png"),
        help="Output image path. Default: plot/throughput_metrics.png",
    )
    return parser.parse_args()


# Algorithm name mapping
ALGO_NAMES = {
    "bestfit": "BestFit",
    "drf": "DRF",
    "gandivaspike": "Gandiva",
    "glass": "GLaSS",
    "p2c": "P2C",
    "roundrobin": "RR",
}

ALGO_ORDER = ["GLaSS", "Gandiva", "BestFit", "DRF", "P2C", "RR"]


def parse_summary_file(filepath: Path) -> dict:
    """
    Parse throughput summary file and extract data.
    
    Returns:
        Dict with structure: {seed: {algo: {throughput, migrations, cost, per_task}}}
    """
    data = defaultdict(lambda: defaultdict(dict))
    
    content = filepath.read_text()
    
    # Split by seed sections
    seed_pattern = r"Seed: (\d+) -"
    metric_patterns = {
        "throughput": r"(\w+):\s*Throughput:\s*([\d.]+)\s*tasks/step",
        "migrations": r"(\w+):\s*Total Migrations:\s*(\d+)",
        "cost": r"(\w+):\s*Migration Cost:\s*([\d.]+)",
        "per_task": r"(\w+):\s*Migrations per Task:\s*([\d.]+)",
    }
    
    current_seed = None
    
    for line in content.split("\n"):
        # Check for seed header
        seed_match = re.search(seed_pattern, line)
        if seed_match:
            current_seed = int(seed_match.group(1))
            continue
        
        # Parse metrics (remove leading whitespace from line)
        line = line.strip()
        if current_seed is not None:
            # Throughput
            match = re.search(metric_patterns["throughput"], line)
            if match:
                algo_raw = match.group(1).lower()
                algo = ALGO_NAMES.get(algo_raw, algo_raw.upper())
                if algo not in data[current_seed]:
                    data[current_seed][algo] = {}
                data[current_seed][algo]["throughput"] = float(match.group(2))
            
            # Total Migrations
            match = re.search(metric_patterns["migrations"], line)
            if match:
                algo_raw = match.group(1).lower()
                algo = ALGO_NAMES.get(algo_raw, algo_raw.upper())
                if algo not in data[current_seed]:
                    data[current_seed][algo] = {}
                data[current_seed][algo]["migrations"] = int(match.group(2))
            
            # Migration Cost
            match = re.search(metric_patterns["cost"], line)
            if match:
                algo_raw = match.group(1).lower()
                algo = ALGO_NAMES.get(algo_raw, algo_raw.upper())
                if algo not in data[current_seed]:
                    data[current_seed][algo] = {}
                data[current_seed][algo]["cost"] = float(match.group(2))
            
            # Migrations per Task
            match = re.search(metric_patterns["per_task"], line)
            if match:
                algo_raw = match.group(1).lower()
                algo = ALGO_NAMES.get(algo_raw, algo_raw.upper())
                if algo not in data[current_seed]:
                    data[current_seed][algo] = {}
                data[current_seed][algo]["per_task"] = float(match.group(2))
    
    return dict(data)


def compute_aggregated_stats(data: dict) -> dict:
    """
    Compute aggregated statistics across all seeds.
    
    Returns:
        Dict with structure: {metric: {algo: {mean, std}}}
    """
    aggregated = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    
    # Collect values across seeds
    for seed, algos in data.items():
        for algo, metrics in algos.items():
            for metric_name, value in metrics.items():
                aggregated[metric_name][algo]["values"].append(value)
    
    # Compute mean and std
    result = {}
    for metric_name, algos in aggregated.items():
        result[metric_name] = {}
        for algo, vals in algos.items():
            values = vals["values"]
            result[metric_name][algo] = {
                "mean": np.mean(values),
                "std": np.std(values),
            }
    
    return result


def create_table_figure(data: dict, aggregated: dict, output: Path) -> Path:
    """Create a comprehensive table figure with all throughput metrics."""
    output.parent.mkdir(parents=True, exist_ok=True)
    
    seeds = sorted(data.keys())
    metrics_info = [
        ("throughput", "Throughput (tasks/step)", True),  # Higher is better
        ("migrations", "Total Migrations", False),  # Lower is better
        ("cost", "Total Migration Cost", False),  # Lower is better
        ("per_task", "Migrations per Task", False),  # Lower is better
    ]
    
    # Filter and order algorithms
    algos = [a for a in ALGO_ORDER if a in aggregated.get("throughput", {})]
    
    # Create figure with subplots for each metric
    fig, axes = plt.subplots(4, 1, figsize=(14, 14))
    fig.suptitle("Throughput and Migration Metrics Summary", fontsize=16, fontweight="bold", y=0.995)
    
    for ax_idx, (metric_key, metric_label, higher_better) in enumerate(metrics_info):
        ax = axes[ax_idx]
        ax.set_title(metric_label, fontsize=12, fontweight="bold", pad=10)
        
        # Build table data
        col_labels = ["Algorithm"] + [f"Seed {s}" for s in seeds] + ["Mean ± Std", "Rank"]
        
        table_data = []
        algo_means = {}
        
        for algo in algos:
            row = [algo]
            for seed in seeds:
                val = data.get(seed, {}).get(algo, {}).get(metric_key, 0)
                if metric_key == "migrations":
                    row.append(f"{int(val)}")
                else:
                    row.append(f"{val:.4f}")
            
            # Aggregated stats
            stats = aggregated.get(metric_key, {}).get(algo, {})
            mean_val = stats.get("mean", 0)
            std_val = stats.get("std", 0)
            if metric_key == "migrations":
                row.append(f"{mean_val:.1f} ± {std_val:.1f}")
            else:
                row.append(f"{mean_val:.4f} ± {std_val:.4f}")
            algo_means[algo] = mean_val
            
            row.append("")  # Placeholder for rank
            table_data.append(row)
        
        # Determine rankings
        if higher_better:
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
        table.set_fontsize(9)
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
                    if j > 0:
                        table[(i, j)].set_facecolor("#C6EFCE")
            elif row[-1] == "2":
                for j in range(len(col_labels)):
                    if j > 0:
                        table[(i, j)].set_facecolor("#FFEB9C")
    
    plt.tight_layout(rect=[0, 0, 1, 0.99])
    fig.savefig(output, dpi=150, bbox_inches="tight")
    plt.close(fig)
    
    return output


def print_summary(aggregated: dict):
    """Print text summary of results."""
    print("\n" + "=" * 70)
    print("AGGREGATED THROUGHPUT METRICS (Mean ± Std across all seeds)")
    print("=" * 70)
    
    metrics = [
        ("throughput", "Throughput (tasks/step) - higher is better", True),
        ("migrations", "Total Migrations - lower is better", False),
        ("cost", "Total Migration Cost - lower is better", False),
        ("per_task", "Migrations per Task - lower is better", False),
    ]
    
    for metric_key, metric_desc, higher_better in metrics:
        print(f"\n{metric_desc}")
        print("-" * 50)
        
        algos = aggregated.get(metric_key, {})
        
        # Sort by performance
        if higher_better:
            sorted_algos = sorted(algos.items(), key=lambda x: -x[1]["mean"])
        else:
            sorted_algos = sorted(algos.items(), key=lambda x: x[1]["mean"])
        
        for rank, (algo, stats) in enumerate(sorted_algos, 1):
            mean_val = stats["mean"]
            std_val = stats["std"]
            if metric_key == "migrations":
                print(f"  {rank}. {algo:10s}: {mean_val:.1f} ± {std_val:.1f}")
            else:
                print(f"  {rank}. {algo:10s}: {mean_val:.4f} ± {std_val:.4f}")
    
    print("\n" + "=" * 70)


def main() -> None:
    args = parse_args()
    
    # Find summary file
    if args.summary_file is None:
        results_dir = Path("results")
        summary_files = sorted(results_dir.glob("throughput_summary_*.txt"))
        if not summary_files:
            print("Error: No throughput summary files found in results/")
            return
        summary_file = summary_files[-1]
        print(f"Using latest summary file: {summary_file}")
    else:
        summary_file = args.summary_file
    
    if not summary_file.exists():
        print(f"Error: {summary_file} not found")
        return
    
    # Parse data
    data = parse_summary_file(summary_file)
    if not data:
        print("Error: No data could be parsed from summary file")
        print("Check that the file contains properly formatted seed sections with metrics")
        return
    
    print(f"Parsed data for {len(data)} seeds: {sorted(data.keys())}")
    
    # Compute aggregated statistics
    aggregated = compute_aggregated_stats(data)
    
    if not aggregated:
        print("Error: No aggregated data could be computed")
        return
    
    # Print text summary
    print_summary(aggregated)
    
    # Create table figure
    output = create_table_figure(data, aggregated, args.output)
    print(f"\nSaved throughput metrics table to {output}")


if __name__ == "__main__":
    main()
