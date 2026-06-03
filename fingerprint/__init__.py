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
from .extractor import extract_fingerprint_from_W, extract_fingerprint_from_spikes
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
        mean_injection_trace: (T,) validation/batch mean injection trace.
        traffic_sequence: compatibility property alias for mean_injection_trace.
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
        sample_measured_injection_trace: optional (T,) one-image measured ground truth trace.
        sample_index: source dataset index for the measured sample.
        sample_label: source label for the measured sample.
        sample_path: source sample identifier.
        meta: free-form metadata.
    """

    mean_injection_trace: np.ndarray
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
    sample_measured_injection_trace: np.ndarray = field(
        default_factory=lambda: np.zeros(0, dtype=np.float32)
    )
    sample_index: int = -1
    sample_label: int = -1
    sample_path: str = ""
    meta: Dict[str, str] = field(default_factory=dict)

    @property
    def traffic_sequence(self) -> np.ndarray:
        return self.mean_injection_trace

    @property
    def sample_measured_injection_traces(self) -> np.ndarray:
        if self.sample_measured_injection_trace.size == 0:
            return np.zeros((0, 0), dtype=np.float32)
        return self.sample_measured_injection_trace.reshape(1, -1)

    @property
    def sample_indices(self) -> np.ndarray:
        return np.asarray([], dtype=np.int32) if self.sample_index < 0 else np.asarray([self.sample_index], dtype=np.int32)

    @property
    def sample_labels(self) -> np.ndarray:
        return np.asarray([], dtype=np.int32) if self.sample_label < 0 else np.asarray([self.sample_label], dtype=np.int32)

    @property
    def sample_paths(self) -> np.ndarray:
        return np.asarray([], dtype=str) if not self.sample_path else np.asarray([self.sample_path], dtype=str)


def effective_traffic_trace(fp: Fingerprint) -> np.ndarray:
    """Single source of truth for the trace consumed by both engine and STPS forecast.

    Prefers per-image measured trace when available; falls back to mean trace.
    Returning the same array for forecasting and simulation execution avoids
    Stage-B phase-shifting against one trace while engine congests on another.
    """
    sample = fp.sample_measured_injection_trace
    if sample.size > 0:
        return sample
    return fp.traffic_sequence


__all__ = [
    "Fingerprint",
    "MicroPopulation",
    "SparsityMask",
    "effective_traffic_trace",
    "extract_fingerprint_from_W",
    "extract_fingerprint_from_spikes",
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
