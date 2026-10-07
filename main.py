#!/usr/bin/env python3
"""Run a fixed single-card scene or online multi-card baseline requests."""
import argparse
import json
from pathlib import Path

from simulation.engine import run_simulation
from simulation.cluster_engine import run_cluster_simulation
from schedule.baselines import POLICIES


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--scenario", type=Path, help="fixed single-card mapping scene")
    mode.add_argument("--cluster-scenario", type=Path, help="online arrival trace over multiple cards")
    parser.add_argument("--policy", choices=(*POLICIES, 'STPS'), default="RR")
    parser.add_argument("--scheduler-seed", type=int, help="override scenario scheduler seed")
    parser.add_argument("--output-dir", type=Path, required=True, help="new or empty output directory")
    parser.add_argument("--trace", action="store_true", help="write transition events.csv (small runs)")
    parser.add_argument("--no-report", action="store_true", help="skip HTML/SVG rendering")
    args = parser.parse_args(argv)
    try:
        if args.cluster_scenario:
            result = run_cluster_simulation(args.cluster_scenario, args.output_dir, policy=args.policy,
                                            seed=args.scheduler_seed, trace=args.trace, report=not args.no_report)
        else:
            if args.policy != "RR" or args.scheduler_seed is not None:
                parser.error("policy and scheduler seed apply to --cluster-scenario")
            result = run_simulation(args.scenario, args.output_dir, trace=args.trace, report=not args.no_report)
    except (OSError, ValueError, KeyError) as exc:
        parser.error(str(exc))
    print(json.dumps({"status": result.status, "ticks_executed": result.ticks_executed,
                      "timing": result.summary["timing"], "output_dir": str(result.output_dir),
                      "totals": result.summary["totals"]}, ensure_ascii=False))
    return 0 if result.status == "completed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
