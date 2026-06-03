"""Round-trip persistence for Fingerprint via .npz (docs/fingerprint.md §7).

Schema (new, replaces legacy E/beta/centrality_last/K_mean keys):
    mean_injection_trace : (T,) float32
    global_burstiness : scalar float32
    max_centrality    : (V',) float32
    mean_components   : scalar float32
    T                 : scalar int32
    neuron_count      : scalar int32
    state_size_mb     : scalar float32
    complexity_ratio  : scalar float32
    compute_sequence  : (T,) float32         (may be zeros if not computed)
    centrality_var    : (T,) float32         (legacy ablation field, may be empty)
    sample_measured_injection_trace : (T,) float32 (optional)
    sample_index      : scalar int32 (optional)
    sample_label      : scalar int32 (optional)
    sample_path       : string (optional)
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
        mean_injection_trace=fp.mean_injection_trace.astype(np.float32),
        global_burstiness=np.float32(fp.global_burstiness),
        max_centrality=fp.max_centrality.astype(np.float32),
        mean_components=np.float32(fp.mean_components),
        T=np.int32(fp.T),
        neuron_count=np.int32(fp.neuron_count),
        state_size_mb=np.float32(fp.state_size_mb),
        complexity_ratio=np.float32(fp.complexity_ratio),
        compute_sequence=fp.compute_sequence.astype(np.float32),
        centrality_var=fp.centrality_var.astype(np.float32),
        sample_measured_injection_trace=fp.sample_measured_injection_trace.astype(np.float32),
        sample_index=np.int32(fp.sample_index),
        sample_label=np.int32(fp.sample_label),
        sample_path=np.array(str(fp.sample_path)),
        meta=np.array(json.dumps(fp.meta)),
    )


def load_fingerprint(path: PathLike) -> "Fingerprint":
    from . import Fingerprint  # local import to avoid circular ref

    d = np.load(path, allow_pickle=False)
    if "mean_injection_trace" not in d.files and "traffic_sequence" not in d.files and "E" in d.files:
        return _load_legacy_fingerprint(d)

    meta_raw = d["meta"]
    meta_str = str(meta_raw.item()) if meta_raw.shape == () else str(meta_raw)
    return Fingerprint(
        mean_injection_trace=(
            d["mean_injection_trace"].astype(np.float32)
            if "mean_injection_trace" in d.files
            else d["traffic_sequence"].astype(np.float32)
        ),
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
        sample_measured_injection_trace=(
            d["sample_measured_injection_trace"].astype(np.float32)
            if "sample_measured_injection_trace" in d.files
            else (
                d["sample_measured_injection_traces"][0].astype(np.float32)
                if "sample_measured_injection_traces" in d.files and d["sample_measured_injection_traces"].shape[0] > 0
                else np.zeros(0, dtype=np.float32)
            )
        ),
        sample_index=(
            int(d["sample_index"])
            if "sample_index" in d.files
            else (int(d["sample_indices"][0]) if "sample_indices" in d.files and d["sample_indices"].shape[0] > 0 else -1)
        ),
        sample_label=(
            int(d["sample_label"])
            if "sample_label" in d.files
            else (int(d["sample_labels"][0]) if "sample_labels" in d.files and d["sample_labels"].shape[0] > 0 else -1)
        ),
        sample_path=(
            str(d["sample_path"].item())
            if "sample_path" in d.files
            else (str(d["sample_paths"][0]) if "sample_paths" in d.files and d["sample_paths"].shape[0] > 0 else "")
        ),
        meta=json.loads(meta_str) if meta_str else {},
    )


def _load_legacy_fingerprint(d) -> "Fingerprint":
    from . import Fingerprint

    T = int(d["T"])
    meta_raw = d["meta"] if "meta" in d.files else np.array("{}")
    meta_str = str(meta_raw.item()) if getattr(meta_raw, "shape", ()) == () else str(meta_raw)
    mean_injection_trace = d["E"].astype(np.float32)
    return Fingerprint(
        mean_injection_trace=mean_injection_trace,
        global_burstiness=float(d["beta"]),
        max_centrality=d["centrality_last"].astype(np.float32),
        mean_components=float(d["K_mean"]),
        T=T,
        neuron_count=int(d["neuron_count"]) if "neuron_count" in d.files else int(d["centrality_last"].shape[0]),
        state_size_mb=float(d["state_size_mb"]) if "state_size_mb" in d.files else 12.0,
        complexity_ratio=float(d["complexity_ratio"]) if "complexity_ratio" in d.files else 1.0,
        compute_sequence=np.zeros(T, dtype=np.float32),
        centrality_var=(
            d["centrality_var"].astype(np.float32)
            if "centrality_var" in d.files
            else np.zeros(0, dtype=np.float32)
        ),
        sample_measured_injection_trace=np.zeros(0, dtype=np.float32),
        sample_index=-1,
        sample_label=-1,
        sample_path="",
        meta=json.loads(meta_str) if meta_str else {},
    )
