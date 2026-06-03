"""DTDG fingerprint extractor (docs/fingerprint.md §5, §Step 5).

Operates on either:
    - new 2D edge tensor: W ∈ R^{T × V' × V' × 2}
        channel 0 = Traffic^(t)_ij, channel 1 = Compute^(t)_ij.
    - legacy Traffic-only: W ∈ R^{T × V × V} (back-compat for synthetic / dtdg).

Outputs three fingerprints (§5.1, §5.2, §5.3):
    mean_components K̄ — average #active connected components over T.
    traffic_sequence E^(t) and global_burstiness β = max E / mean E.
    max_centrality c*_max = max over t of in-eigenvector centrality c^(t).

Plus the legacy centrality_var timeline (kept for ablation parity).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Dict, Optional, Tuple

import numpy as np

from .centrality import power_iteration_in_eigen_centrality

if TYPE_CHECKING:
    from . import Fingerprint


def _split_traffic_compute(W: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """Return (Traffic, Compute) of shape (T, V, V) each.

    Accepts (T, V, V) → Compute is zeros; or (T, V, V, 2) → split channel.
    """
    if W.ndim == 3:
        T = np.asarray(W, dtype=np.float32)
        return T, np.zeros_like(T)
    if W.ndim == 4 and W.shape[-1] == 2:
        return (
            np.asarray(W[..., 0], dtype=np.float32),
            np.asarray(W[..., 1], dtype=np.float32),
        )
    raise ValueError(f"W must be (T,V,V) or (T,V,V,2); got shape {W.shape}")


def _active_connected_components(adj: np.ndarray) -> int:
    """Count connected components containing ≥1 active node (any incident edge)."""
    V = adj.shape[0]
    undirected = adj | adj.T
    active = undirected.any(axis=1)
    if not active.any():
        return 0
    visited = np.zeros(V, dtype=bool)
    components = 0
    for start in range(V):
        if visited[start] or not active[start]:
            continue
        components += 1
        stack = [start]
        while stack:
            u = stack.pop()
            if visited[u]:
                continue
            visited[u] = True
            for v in np.flatnonzero(undirected[u]):
                if not visited[v]:
                    stack.append(int(v))
    return components


def extract_fingerprint_from_W(
    W: np.ndarray,
    neuron_count: int,
    state_size_mb: float,
    complexity_ratio: float = 1.0,
    meta: Optional[Dict[str, str]] = None,
    centrality_iters: int = 100,
    edge_threshold: float = 0.0,
) -> "Fingerprint":
    """Compute (E, β, c*_max, K̄) from a DTDG weight tensor.

    Args:
        W: (T,V,V) or (T,V,V,2) non-negative weight tensor.
        neuron_count: V' (post-slicing population count) by default.
        state_size_mb: source-model state size in MB.
        complexity_ratio: relative compute-intensity multiplier.
        meta: optional metadata.
        centrality_iters: power-iteration cap per snapshot.
        edge_threshold: ε (§5.1). Edges with Traffic ≤ ε are filtered out
            before connected-components / centrality are computed. Default 0
            is the "any nonzero" rule used throughout the doc.
    """
    from . import Fingerprint

    Traffic, Compute = _split_traffic_compute(W)
    if Traffic.ndim != 3 or Traffic.shape[1] != Traffic.shape[2]:
        raise ValueError(f"Traffic must be (T,V,V); got shape {Traffic.shape}")

    T, V, _ = Traffic.shape

    traffic_sequence = Traffic.sum(axis=(1, 2)).astype(np.float32)
    compute_sequence = Compute.sum(axis=(1, 2)).astype(np.float32)
    mean_E = float(traffic_sequence.mean()) if T > 0 else 0.0
    global_burstiness = (
        float(traffic_sequence.max() / mean_E) if mean_E > 1e-12 else 1.0
    )

    # Per-step centrality timeline (used for both var and time-global max).
    cmat = np.zeros((T, V), dtype=np.float32) if V > 0 else np.zeros((T, 0), np.float32)
    cvar = np.zeros(T, dtype=np.float32)
    for t in range(T):
        snapshot = Traffic[t]
        if edge_threshold > 0.0:
            snapshot = np.where(snapshot > edge_threshold, snapshot, 0.0)
        c_t = power_iteration_in_eigen_centrality(snapshot, iters=centrality_iters)
        cmat[t] = c_t
        cvar[t] = float(c_t.var())

    if V > 0 and T > 0:
        max_centrality = cmat.max(axis=0).astype(np.float32)
    else:
        max_centrality = np.zeros(V, dtype=np.float32)

    if T > 0:
        ks = []
        for t in range(T):
            adj = Traffic[t] > edge_threshold
            ks.append(_active_connected_components(adj))
        mean_components = float(np.mean(ks)) if ks else 0.0
    else:
        mean_components = 0.0

    return Fingerprint(
        mean_injection_trace=traffic_sequence,
        global_burstiness=global_burstiness,
        max_centrality=max_centrality,
        mean_components=mean_components,
        T=int(T),
        neuron_count=int(neuron_count),
        state_size_mb=float(state_size_mb),
        complexity_ratio=float(complexity_ratio),
        compute_sequence=compute_sequence,
        centrality_var=cvar,
        meta=dict(meta or {}),
    )


def extract_fingerprint_from_spikes(
    E: np.ndarray,
    neuron_count: int,
    state_size_mb: float,
    complexity_ratio: float = 1.0,
    meta: Optional[Dict[str, str]] = None,
) -> "Fingerprint":
    """Build a Fingerprint from a precomputed (T,) spike-count timeline.

    Single-card deployment assumption (docs/traffic_TODO.md): E^(t) is the
    val-set sample mean of per-tick spike counts. K̄ and c*_max degenerate
    (K̄=1.0, c*_max = uniform), since slicing/edges are no longer modeled.
    """
    from . import Fingerprint

    E = np.asarray(E, dtype=np.float32)
    if E.ndim != 1:
        raise ValueError(f"E must be a 1D spike-count timeline; got shape {E.shape}")
    T = int(E.shape[0])
    mean_E = float(E.mean()) if T > 0 else 0.0
    global_burstiness = float(E.max() / mean_E) if mean_E > 1e-12 else 1.0

    if int(neuron_count) <= 0:
        raise ValueError("neuron_count must be positive")
    V = int(neuron_count)
    max_centrality = np.full(V, 1.0 / V, dtype=np.float32)

    return Fingerprint(
        mean_injection_trace=E,
        global_burstiness=global_burstiness,
        max_centrality=max_centrality,
        mean_components=1.0,
        T=T,
        neuron_count=int(neuron_count),
        state_size_mb=float(state_size_mb),
        complexity_ratio=float(complexity_ratio),
        compute_sequence=np.zeros(T, dtype=np.float32),
        centrality_var=np.zeros(T, dtype=np.float32),
        meta=dict(meta or {}),
    )
