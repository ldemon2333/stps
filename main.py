#!/usr/bin/env python3
"""
Main entry point for SNN load-balancing simulation.

Supports multiple scheduling algorithms through a pluggable architecture.
Use --scheduler to select the algorithm, --help for all options.

Examples:
    # Run static baseline
    python main.py --scheduler static --cards 4 --tasks 100

    # Run GLaSS dynamic scheduler  
    python main.py --scheduler glass --cards 4 --tasks 100 --arrival-mode bursty

    # List available schedulers
    python main.py --list-schedulers
"""
from __future__ import annotations

import argparse
import sys

from schedule import list_schedulers
from simulation.engine import run_simulation


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Run SNN load-balancing simulation with pluggable schedulers",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --scheduler static --cards 4 --tasks 100 --steps 60
  %(prog)s --scheduler glass --cards 4 --tasks 100 --arrival-mode bursty
  %(prog)s --list-schedulers
        """,
    )
    
    # Scheduler selection
    parser.add_argument(
        "--scheduler",
        type=str,
        default="static",
        help="Scheduling algorithm to use (default: static). Use --list-schedulers to see options.",
    )
    parser.add_argument(
        "--list-schedulers",
        action="store_true",
        help="List available schedulers and exit",
    )
    
    # Backward compatibility alias
    parser.add_argument(
        "--mode",
        type=str,
        choices=["dynamic", "static"],
        help="(Deprecated) Use --scheduler instead. Maps to 'glass' or 'static'.",
    )
    
    # Simulation configuration
    parser.add_argument(
        "--cards",
        type=int,
        default=4,
        help="Number of neuromorphic accelerator cards (default: 4)",
    )
    parser.add_argument(
        "--tasks",
        type=int,
        default=100,
        help="Total number of tasks to schedule (default: 100)",
    )
    parser.add_argument(
        "--steps",
        type=int,
        default=60,
        help="Simulation duration in time steps (default: 60)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for reproducibility (default: 42)",
    )
    
    # Workload configuration
    parser.add_argument(
        "--arrival-mode",
        type=str,
        default="poisson",
        choices=["poisson", "bursty", "mixed"],
        help="Task arrival pattern (default: poisson)",
    )
    
    # Output configuration
    parser.add_argument(
        "--log-dir",
        type=str,
        default="log",
        help="Directory for log files (default: log)",
    )
    parser.add_argument(
        "--data-dir",
        type=str,
        default="data",
        help="Directory for data files (default: data)",
    )
    
    # Load calculation weights
    parser.add_argument(
        "--alpha",
        type=float,
        default=1.0,
        help="Weight for spike count in load calculation (default: 1.0)",
    )
    parser.add_argument(
        "--beta",
        type=float,
        default=0.01,
        help="Weight for synaptic operations in load calculation (default: 0.01)",
    )
    
    # GLaSS-specific parameters
    parser.add_argument(
        "--card-capacity",
        type=float,
        default=5000.0,
        help="Card capacity for normalized load calculation in GLaSS (default: 5000.0)",
    )
    parser.add_argument(
        "--gamma",
        type=float,
        default=1.5,
        help="Heat preference factor for ROI calculation in GLaSS (default: 1.5)",
    )
    parser.add_argument(
        "--data-output",
        type=str,
        default=None,
        help="Filename prefix for data outputs (no extension). If omitted, timestamp-based names are used.",
    )
    
    # Placement strategy parameters
    parser.add_argument(
        "--placement-strategy",
        type=str,
        default="bestfit",
        choices=["bestfit", "p2c", "drf", "rr"],
        help="Placement strategy for task placement and migration (default: bestfit). "
             "Use with GLaSS to customize both initial placement and migration logic.",
    )
    parser.add_argument(
        "--load-metric",
        type=str,
        default="weighted",
        choices=["weighted", "drf", "tasks"],
        help="Load metric for P2C strategy (default: weighted)",
    )
    
    return parser.parse_args()


def main() -> int:
    """Main entry point."""
    args = parse_args()
    
    # Handle --list-schedulers
    if args.list_schedulers:
        print("Available schedulers:")
        for name in sorted(list_schedulers()):
            print(f"  - {name}")
        return 0
    
    # Handle deprecated --mode argument
    scheduler = args.scheduler
    if args.mode:
        print(f"Warning: --mode is deprecated. Use --scheduler instead.", file=sys.stderr)
        scheduler = "glass" if args.mode == "dynamic" else "static"
    
    # Run simulation
    try:
        metrics = run_simulation(
            scheduler=scheduler,
            cards=args.cards,
            tasks=args.tasks,
            steps=args.steps,
            seed=args.seed,
            log_dir=args.log_dir,
            data_dir=args.data_dir,
            data_output=args.data_output,
            arrival_mode=args.arrival_mode,
            alpha=args.alpha,
            beta=args.beta,
            card_capacity=args.card_capacity,
            gamma=args.gamma,
            placement_strategy=args.placement_strategy,
            load_metric=args.load_metric,
        )
        
        # Return 0 on success
        return 0
        
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        print(f"Use --list-schedulers to see available options.", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Simulation failed: {e}", file=sys.stderr)
        raise


if __name__ == "__main__":
    sys.exit(main())