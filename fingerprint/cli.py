"""CLI for offline fingerprint extraction (synthetic path).

Examples:
    python -m fingerprint.cli --synthetic --T 32 --beta 4 --K 2 \
        --out npz/synthetic_bursty.npz

For real-model extraction use the dedicated adapters:
    python -m fingerprint.extract_spikformer ...
    python -m fingerprint.extract_spikingresformer ...
"""
from __future__ import annotations

import argparse
import sys

from . import (
    Fingerprint,
    load_fingerprint,
    make_synthetic_fingerprint,
    save_fingerprint,
)


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Offline DTDG fingerprint extractor")
    p.add_argument("--out", required=True, help="Output .npz path")
    p.add_argument("--T", type=int, default=32, help="DTDG window size")
    p.add_argument("--synthetic", action="store_true",
                   help="Generate a synthetic fingerprint (default if no --model)")
    p.add_argument("--beta", type=float, default=4.0,
                   help="Synthetic burstiness target (only with --synthetic)")
    p.add_argument("--K", type=int, default=1,
                   help="Synthetic active-connected-components target")
    p.add_argument("--var", type=float, default=0.05,
                   help="Synthetic centrality-variance target")
    p.add_argument("--V", type=int, default=16,
                   help="Synthetic population count")
    p.add_argument("--neuron-count", type=int, default=512)
    p.add_argument("--state-size-mb", type=float, default=12.0)
    p.add_argument("--complexity-ratio", type=float, default=1.0)
    p.add_argument("--seed", type=int, default=None)
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

    fp = make_synthetic_fingerprint(
        beta_target=args.beta,
        K=args.K,
        var_target=args.var,
        T=args.T,
        V=args.V,
        neuron_count=args.neuron_count,
        state_size_mb=args.state_size_mb,
        complexity_ratio=args.complexity_ratio,
        seed=args.seed,
        meta={"source": "synthetic", "beta_target": str(args.beta), "K": str(args.K)},
    )

    save_fingerprint(args.out, fp)
    loaded = load_fingerprint(args.out)
    print(_summary(loaded))
    return 0


def _summary(fp: Fingerprint) -> str:
    return (
        f"Fingerprint(T={fp.T}, V'={fp.max_centrality.shape[0]}, "
        f"beta={fp.global_burstiness:.3f}, K_mean={fp.mean_components:.2f}, "
        f"E_max={float(fp.traffic_sequence.max()):.2f}, "
        f"E_mean={float(fp.traffic_sequence.mean()):.2f}, "
        f"meta={fp.meta})"
    )


if __name__ == "__main__":
    sys.exit(main())
