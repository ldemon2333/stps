"""Offline DTDG workload fingerprinting (docs/fingerprint.md).

Public API:
- Fingerprint: dataclass with the four physical fingerprints
  (mean_components, traffic_sequence, global_burstiness, max_centrality)
- save_fingerprint / load_fingerprint: .npz persistence (new schema, §7)
- extract_fingerprint_from_W: extractor over a (T, V, V, 2) DTDG tensor
  (channel 0 = Traffic, channel 1 = Compute) or legacy (T, V, V) Traffic-only
- make_synthetic_fingerprint: synthetic generator
- split_layer / SparsityMask: §2.3 hardware-aware slicing + §4 mask cases
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict

import numpy as np

from .centrality import power_iteration_in_eigen_centrality
from .extractor import extract_fingerprint_from_W
from .io import load_fingerprint, save_fingerprint
from .mask import (
    SparsityMask,
    mask_conv2d,
    mask_identity,
    mask_linear,
    mask_pruned,
)
from .slicing import MicroPopulation, split_layer
from .synth import make_synthetic_fingerprint


@dataclass(frozen=True)
class Fingerprint:
    """Hardware-isomorphic spatio-temporal fingerprint (docs/fingerprint.md §7).

    Fields named per §7 final schema:
        traffic_sequence: (T,) E^(t), per-tick total NoC flits expectation.
        global_burstiness: β = max(E)/mean(E).
        max_centrality: c*_max ∈ R^{V'}, time-global max of in-eigenvector
            centrality (max over t of c^(t)).
        mean_components: K̄, average active connected components over T.
        T: window size (drives Task duration in scheduler steps).
        neuron_count: V' (post-slicing micro-population count) by default,
            kept for placement-footprint compatibility.
        state_size_mb: in-memory state size (placement footprint).
        complexity_ratio: relative compute-intensity multiplier.
        compute_sequence: optional (T,) Compute^(t) total SOPs timeline; may
            be empty array if Compute channel was not built.
        centrality_var: optional (T,) per-step centrality variance; legacy
            field retained because schedulers/baselines used it. May be empty.
        meta: free-form metadata.
    """

    traffic_sequence: np.ndarray
    global_burstiness: float
    max_centrality: np.ndarray
    mean_components: float
    T: int
    neuron_count: int
    state_size_mb: float
    complexity_ratio: float
    compute_sequence: np.ndarray = field(
        default_factory=lambda: np.zeros(0, dtype=np.float32)
    )
    centrality_var: np.ndarray = field(
        default_factory=lambda: np.zeros(0, dtype=np.float32)
    )
    meta: Dict[str, str] = field(default_factory=dict)


__all__ = [
    "Fingerprint",
    "MicroPopulation",
    "SparsityMask",
    "extract_fingerprint_from_W",
    "load_fingerprint",
    "make_synthetic_fingerprint",
    "mask_conv2d",
    "mask_identity",
    "mask_linear",
    "mask_pruned",
    "power_iteration_in_eigen_centrality",
    "save_fingerprint",
    "split_layer",
]
