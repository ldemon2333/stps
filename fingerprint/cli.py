"""Generate an explicitly synthetic workload or convert a same-source TNN2 trace."""
import argparse
from pathlib import Path

import numpy as np

from .workload import from_edge_tensor, make_sparse_workload, save_workload


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    synthetic = sub.add_parser("synthetic", help="sparse independent SOP/traffic test stimulus")
    synthetic.add_argument("--out", type=Path, required=True)
    synthetic.add_argument("--seed", type=int, default=0)
    synthetic.add_argument("--ticks", type=int, default=8)
    synthetic.add_argument("--populations", type=int, default=3)
    convert = sub.add_parser("from-tensor", help="convert explicit traffic+compute, not E-only")
    convert.add_argument("--tensor", type=Path, required=True, help=".npy T x N x N x 2")
    convert.add_argument("--pop-size", type=int, nargs="+", required=True)
    convert.add_argument("--state-size-mb", type=float, required=True)
    convert.add_argument("--source", required=True, help="model/checkpoint/sample provenance")
    convert.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "synthetic":
            workload = make_sparse_workload(seed=args.seed, T=args.ticks, populations=args.populations)
        else:
            workload = from_edge_tensor(np.load(args.tensor, allow_pickle=False), args.pop_size,
                                        args.state_size_mb, args.source,
                                        {"tensor_path": str(args.tensor)})
        save_workload(args.out, workload)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(f"saved {args.out}: T={workload.T}, MicroPopulations={workload.population_count}, "
          f"flits={workload.totals['quantized_flits']}, SOP={workload.totals['compute_sops']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
