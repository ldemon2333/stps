#!/usr/bin/env python3
"""Plot CV (Coefficient of Variation) CDF for different schedulers/arrival modes.

Usage:
    python script/plot_cv_cdf.py <experiment_name> [cards]
    
Example:
    python script/plot_cv_cdf.py main4 4
    python script/plot_cv_cdf.py main16 16
    
The script reads raw metrics from previously completed simulations and generates:
    - CDF plot (X: CV value, Y: cumulative probability)
    - Summary statistics table (mean, median, p95, std)
"""
import csv
import sys
from pathlib import Path
from typing import Dict, List, Tuple
import numpy as np

try:
    import matplotlib.pyplot as plt
    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False

ROOT = Path(__file__).resolve().parents[1]


def compute_cv_stats(cv_values: List[float]) -> Dict:
    """Compute statistics from a list of CV values."""
    if not cv_values:
        return {'mean': 0, 'median': 0, 'p95': 0, 'std': 0, 'n': 0}
    
    arr = np.array(cv_values)
    return {
        'n': len(cv_values),
        'mean': float(np.mean(arr)),
        'median': float(np.median(arr)),
        'p95': float(np.percentile(arr, 95)),
        'std': float(np.std(arr)),
    }


def load_raw_cv_data(raw_path: Path) -> Dict[Tuple[str, str], List[float]]:
    """Load CV values from raw CSV, grouped by (scheduler, arrival_mode).
    
    Returns: {(scheduler, arrival_mode): [cv1, cv2, ...], ...}
    """
    data = {}
    
    if not raw_path.exists():
        print(f"⚠ Raw CSV not found: {raw_path}")
        return data
    
    with open(raw_path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                scheduler = row.get('scheduler', 'unknown').strip()
                arrival = row.get('arrival_mode', 'unknown').strip()
                cv = float(row.get('card_cv', 'nan'))
                
                if cv != cv:  # Skip NaN
                    continue
                
                key = (scheduler, arrival)
                if key not in data:
                    data[key] = []
                data[key].append(cv)
            except (ValueError, KeyError):
                pass
    
    return data


def plot_cv_cdf(exp: str, cards: int = 4):
    """Generate CDF plot and print statistics."""
    raw_path = ROOT / f"data/q2/{exp}_bw9e5_d16_raw.csv"
    
    if not raw_path.exists():
        print(f"❌ Raw CSV not found: {raw_path}")
        return
    
    # Load data
    cv_by_group = load_raw_cv_data(raw_path)
    
    if not cv_by_group:
        print("❌ No CV data found in raw CSV")
        return
    
    # Print statistics table
    print(f"\n{'Experiment':20s} | {'Scheduler':16s} | {'Arrival':8s} | "
          f"{'N':4s} | CV Mean | CV Median | CV p95 | CV Std")
    print("-" * 105)
    
    for (scheduler, arrival), cv_vals in sorted(cv_by_group.items()):
        stats = compute_cv_stats(cv_vals)
        print(f"{exp:20s} | {scheduler:16s} | {arrival:8s} | "
              f"{stats['n']:4d} | {stats['mean']:7.4f} | {stats['median']:9.4f} | "
              f"{stats['p95']:6.4f} | {stats['std']:6.4f}")
    
    # Plot CDFs by arrival mode
    if not HAS_MATPLOTLIB:
        print("\n⚠ matplotlib not available; skipping CDF plots.")
        return
    
    # Group by arrival mode
    by_arrival = {}
    for (scheduler, arrival), cv_vals in cv_by_group.items():
        if arrival not in by_arrival:
            by_arrival[arrival] = {}
        by_arrival[arrival][scheduler] = cv_vals
    
    output_dir = ROOT / "figures"
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Create one plot per arrival mode
    for arrival, schedulers_data in sorted(by_arrival.items()):
        fig, ax = plt.subplots(figsize=(12, 6))
        
        for scheduler, cv_vals in sorted(schedulers_data.items()):
            if not cv_vals or len(cv_vals) < 2:
                continue
            
            # Empirical CDF from raw data
            sorted_cv = np.sort(cv_vals)
            cdf = np.arange(1, len(sorted_cv) + 1) / len(sorted_cv)
            
            ax.plot(sorted_cv, cdf, marker='o', markersize=4, alpha=0.7,
                   linewidth=2, label=scheduler)
        
        ax.set_xlabel('CV (Coefficient of Variation)', fontsize=12)
        ax.set_ylabel('Cumulative Probability', fontsize=12)
        ax.set_title(f'CV Distribution CDF — {exp} ({cards} cards, {arrival} arrival)', fontsize=13)
        ax.legend(loc='best', fontsize=10, framealpha=0.95)
        ax.grid(True, alpha=0.3)
        ax.set_ylim([0, 1])
        
        out_path = output_dir / f"cv_cdf_{exp}_{arrival}.png"
        plt.savefig(out_path, dpi=150, bbox_inches='tight')
        print(f"✓ Saved: {out_path}")
        plt.close(fig)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    
    exp = sys.argv[1]
    cards = int(sys.argv[2]) if len(sys.argv) > 2 else 4
    plot_cv_cdf(exp, cards)
