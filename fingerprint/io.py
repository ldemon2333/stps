"""Round-trip persistence for Fingerprint via .npz (docs/fingerprint.md §7).

Schema (new, replaces legacy E/beta/centrality_last/K_mean keys):
    traffic_sequence  : (T,) float32
    global_burstiness : scalar float32
    max_centrality    : (V',) float32
    mean_components   : scalar float32
    T                 : scalar int32
    neuron_count      : scalar int32
    state_size_mb     : scalar float32
    complexity_ratio  : scalar float32
    compute_sequence  : (T,) float32         (may be zeros if not computed)
    centrality_var    : (T,) float32         (legacy ablation field, may be empty)
    meta              : JSON string
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Union

import numpy as np

if TYPE_CHECKING:
    from . import Fingerprint


PathLike = Union[str, Path]


def save_fingerprint(path: PathLike, fp: "Fingerprint") -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        path,
        traffic_sequence=fp.traffic_sequence.astype(np.float32),
        global_burstiness=np.float32(fp.global_burstiness),
        max_centrality=fp.max_centrality.astype(np.float32),
        mean_components=np.float32(fp.mean_components),
        T=np.int32(fp.T),
        neuron_count=np.int32(fp.neuron_count),
        state_size_mb=np.float32(fp.state_size_mb),
        complexity_ratio=np.float32(fp.complexity_ratio),
        compute_sequence=fp.compute_sequence.astype(np.float32),
        centrality_var=fp.centrality_var.astype(np.float32),
        meta=np.array(json.dumps(fp.meta)),
    )


def load_fingerprint(path: PathLike) -> "Fingerprint":
    from . import Fingerprint  # local import to avoid circular ref

    d = np.load(path, allow_pickle=False)
    meta_raw = d["meta"]
    meta_str = str(meta_raw.item()) if meta_raw.shape == () else str(meta_raw)
    return Fingerprint(
        traffic_sequence=d["traffic_sequence"].astype(np.float32),
        global_burstiness=float(d["global_burstiness"]),
        max_centrality=d["max_centrality"].astype(np.float32),
        mean_components=float(d["mean_components"]),
        T=int(d["T"]),
        neuron_count=int(d["neuron_count"]),
        state_size_mb=float(d["state_size_mb"]),
        complexity_ratio=float(d["complexity_ratio"]),
        compute_sequence=(
            d["compute_sequence"].astype(np.float32)
            if "compute_sequence" in d.files
            else np.zeros(int(d["T"]), dtype=np.float32)
        ),
        centrality_var=(
            d["centrality_var"].astype(np.float32)
            if "centrality_var" in d.files
            else np.zeros(0, dtype=np.float32)
        ),
        meta=json.loads(meta_str) if meta_str else {},
    )
