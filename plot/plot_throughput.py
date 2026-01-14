"""Print throughput and migration metrics from summary CSV files."""
from __future__ import annotations

import argparse
import csv
from pathlib import Path
from typing import Dict, List


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Print throughput and migration metrics from scheduler summary CSV files"
    )
    parser.add_argument(
        "csv_files",
        type=Path,
        nargs="*",
        help="Summary CSV files (e.g., glass_summary_*.csv). If omitted, all summary CSVs under data/ are used.",
    )
    return parser.parse_args()


def load_summary_data(csv_path: Path) -> Dict:
    """
    Load summary CSV data.
    
    Returns:
        Dictionary with metrics
    """
    with csv_path.open() as f:
        reader = csv.DictReader(f)
        for row in reader:
            return {
                "scheduler": row["scheduler"],
                "throughput": float(row["throughput"]),
                "total_migrations": int(row["total_migrations"]),
                "total_migration_cost": float(row["total_migration_cost"]),
                "migrations_per_task": float(row["migrations_per_task"]),
            }
    return {}


def infer_label(csv_path: Path) -> str:
    """Infer scheduler name from filename."""
    stem = csv_path.stem
    prefix = stem.split("_")[0]
    return prefix


def main() -> None:
    args = parse_args()
    
    # Determine CSV files to process
    if not args.csv_files:
        csv_files = sorted(Path("data").glob("*_summary_*.csv"))
    else:
        csv_files = args.csv_files
    
    if not csv_files:
        print("No summary CSV files found.")
        return
    
    # Load data
    data_list = []
    for csv_path in csv_files:
        if not csv_path.exists():
            print(f"Warning: {csv_path} not found, skipping.")
            continue
        
        metrics = load_summary_data(csv_path)
        if not metrics:
            print(f"Warning: No data in {csv_path}, skipping.")
            continue
        
        label = infer_label(csv_path)
        data_list.append((label, metrics))
        
        # Print summary in format compatible with throughput_table.py
        print(f"{label}: Throughput: {metrics['throughput']:.4f} tasks/step")
        print(f"{label}: Total Migrations: {metrics['total_migrations']}")
        print(f"{label}: Migration Cost: {metrics['total_migration_cost']:.4f}")
        print(f"{label}: Migrations per Task: {metrics['migrations_per_task']:.4f}")
    
    if not data_list:
        print("No valid data to print.")
        return


if __name__ == "__main__":
    main()
