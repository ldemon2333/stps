#!/usr/bin/env python3
"""Main entry point for SNN load-balancing simulation.

Use ``--scheduler`` to pick an algorithm, ``--list-schedulers`` to enumerate.
"""
from __future__ import annotations

import argparse
import sys

from schedule import list_schedulers
from simulation.engine import run_simulation


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run SNN load-balancing simulation with pluggable schedulers",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --scheduler bestfit --cards 4 --tasks 100 --steps 60
  %(prog)s --scheduler stps --cards 4 --tasks 128 --steps 128 \\
           --arrival-mode bursty --fingerprint-dir npz --bw-max 5e6
  %(prog)s --list-schedulers
        """,
    )

    parser.add_argument("--scheduler", type=str, default="bestfit",
                        help="Scheduling algorithm (default: bestfit). Use --list-schedulers.")
    parser.add_argument("--list-schedulers", action="store_true",
                        help="List available schedulers and exit")

    # Simulation configuration
    parser.add_argument("--cards", type=int, default=4)
    parser.add_argument("--tasks", type=int, default=100)
    parser.add_argument("--steps", type=int, default=60)
    parser.add_argument("--seed", type=int, default=42)

    parser.add_argument("--arrival-mode", type=str, default="poisson",
                        choices=["poisson", "bursty", "mixed"])

    parser.add_argument("--log-dir", type=str, default="log")
    parser.add_argument("--data-dir", type=str, default="data")

    parser.add_argument("--data-output", type=str, default=None,
                        help="Filename prefix for data outputs")

    # Fingerprint-driven load (required: per-tick load is sampled from E^(t)).
    parser.add_argument("--fingerprint-dir", type=str, default="npz",
                        help="Directory of *.npz fingerprints used as the per-tick load source")
    parser.add_argument("--bw-max", type=float, default=1e9,
                        help="NoC bandwidth ceiling per card (STPS forecast threshold)")
    parser.add_argument("--bw-cap", type=float, default=None,
                        help="Per-card per-tick injection ceiling (E^(t) units). "
                             "If unset, engine runs without bandwidth contention; "
                             "if set and --bw-max is at its default, --bw-max is auto-bound to --bw-cap.")
    parser.add_argument("--d-max", type=int, default=16,
                        help="Max phase-shift delay in ticks (STPS only)")
    parser.add_argument("--horizon", type=int, default=64,
                        help="Forecast traffic horizon in ticks (STPS only)")
    parser.add_argument("--centrality-split-threshold", type=float, default=0.2,
                        help="Hotspot-split threshold on centrality (STPS only)")
    parser.add_argument("--queue-depth-factor", type=float, default=8.0,
                        help="NoC injection-queue depth as multiple of bw_cap (buffer before backpressure)")

    # Optional Weights & Biases logging for per-step curves.
    parser.add_argument("--wandb", action="store_true",
                        help="Log per-step card loads and arrivals to Weights & Biases")
    parser.add_argument("--wandb-project", type=str, default="stps-simulation",
                        help="Weights & Biases project name")
    parser.add_argument("--wandb-run-name", type=str, default=None,
                        help="Weights & Biases run name")
    parser.add_argument("--wandb-entity", type=str, default=None,
                        help="Weights & Biases entity/team")
    parser.add_argument("--wandb-mode", type=str, default=None,
                        choices=["online", "offline", "disabled"],
                        help="Weights & Biases mode")

    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.list_schedulers:
        print("Available schedulers:")
        for name in sorted(list_schedulers()):
            print(f"  - {name}")
        return 0

    try:
        # docs/traffic_optim.md §A.4: when user sets --bw-cap but leaves --bw-max
        # at its default, bind bw_max to bw_cap so STPS forecast threshold matches
        # the engine's actual limit.
        bw_max_val = args.bw_max
        if args.bw_cap is not None and args.bw_max == 1e9:
            bw_max_val = args.bw_cap
        run_simulation(
            scheduler=args.scheduler,
            cards=args.cards,
            tasks=args.tasks,
            steps=args.steps,
            seed=args.seed,
            log_dir=args.log_dir,
            data_dir=args.data_dir,
            data_output=args.data_output,
            arrival_mode=args.arrival_mode,
            fingerprint_dir=args.fingerprint_dir,
            bw_max=bw_max_val,
            bw_cap=args.bw_cap,
            d_max=args.d_max,
            horizon=args.horizon,
            centrality_split_threshold=args.centrality_split_threshold,
            queue_depth_factor=args.queue_depth_factor,
            wandb=args.wandb,
            wandb_project=args.wandb_project,
            wandb_run_name=args.wandb_run_name,
            wandb_entity=args.wandb_entity,
            wandb_mode=args.wandb_mode,
        )
        return 0
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        print("Use --list-schedulers to see available options.", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Simulation failed: {e}", file=sys.stderr)
        raise


if __name__ == "__main__":
    sys.exit(main())
