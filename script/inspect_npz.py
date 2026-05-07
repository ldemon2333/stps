"""Pretty-print every key/value pair stored in a fingerprint .npz file.

Usage:
    python script/inspect_npz.py npz/synthetic_bursty.npz
    python script/inspect_npz.py npz/*.npz --full      # show full arrays
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np


def _summarize(val: np.ndarray, full: bool) -> str:
    if val.dtype.kind in ("U", "S", "O"):
        s = val.item() if val.shape == () else val.tolist()
        if isinstance(s, str) and s.startswith("{"):
            try:
                s = json.loads(s)
            except Exception:
                pass
        return repr(s)
    if val.shape == ():
        return f"{val.item()!r}  (scalar, dtype={val.dtype})"
    head = f"shape={val.shape} dtype={val.dtype}"
    if full:
        return head + "\n" + np.array2string(val, threshold=np.inf)
    stats = (f"min={val.min():.4g} max={val.max():.4g} "
             f"mean={val.mean():.4g} sum={val.sum():.4g}")
    preview = np.array2string(val.ravel()[:8], precision=4)
    return f"{head}  {stats}  head={preview}"


def inspect(path: Path, full: bool) -> None:
    print(f"\n=== {path} ===")
    with np.load(path, allow_pickle=True) as data:
        keys = list(data.keys())
        print(f"keys ({len(keys)}): {keys}")
        for k in keys:
            print(f"\n  [{k}]")
            print("   ", _summarize(data[k], full).replace("\n", "\n    "))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("paths", nargs="+", type=Path)
    ap.add_argument("--full", action="store_true",
                    help="Print full arrays instead of stats + head.")
    args = ap.parse_args()
    for p in args.paths:
        inspect(p, args.full)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
