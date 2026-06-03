"""Synthetic fingerprint generator (development workflows, no torch).

Produces a Fingerprint matching the new §7 schema with target (β, K̄, var).
Under the single-card spike-count semantics (docs/traffic_TODO.md),
`traffic_sequence` is the model-wide per-tick spike-count expectation
(unit: spikes / single inference).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Dict, Optional

import numpy as np

if TYPE_CHECKING:
    from . import Fingerprint


def make_synthetic_fingerprint(
    beta_target: float = 4.0,
    K: int = 1,
    var_target: float = 0.05,
    T: int = 32,
    V: int = 16,
    neuron_count: int = 512,
    state_size_mb: float = 12.0,
    complexity_ratio: float = 1.0,
    e_mean: float = 1.0,
    seed: Optional[int] = None,
    meta: Optional[Dict[str, str]] = None,
) -> "Fingerprint":
    """Build a Fingerprint with the requested high-level statistics.

    `beta_target` must satisfy 1 ≤ β < T; values are clipped to [1, T·0.95].
    `e_mean` rescales the resulting timeline so E.mean() == e_mean, matching
    real-model spike-count magnitudes for end-to-end load tests.
    """
    from . import Fingerprint

    rng = np.random.default_rng(seed)
    T = max(int(T), 1)
    V = max(int(V), 1)

    beta_max = max(1.0, T * 0.95)
    beta_target = float(np.clip(beta_target, 1.0, beta_max))
    pulse_idx = int(rng.integers(0, T))
    if T == 1 or beta_target <= 1.0:
        E = np.ones(T, dtype=np.float32)
    else:
        H = beta_target * (T - 1) / (T - beta_target)
        E = np.ones(T, dtype=np.float32)
        E[pulse_idx] = H
    scale = float(e_mean) / float(E.mean()) if E.mean() > 0 else 1.0
    E = (E * scale).astype(np.float32)
    beta = float(E.max() / E.mean()) if E.mean() > 0 else 1.0

    base_c = rng.uniform(0.0, 1.0, size=V).astype(np.float32) + 1e-3
    base_c /= base_c.sum()
    peak = np.zeros(V, dtype=np.float32)
    peak[rng.integers(0, V)] = 1.0
    mix = float(np.clip(np.sqrt(var_target) * np.sqrt(V), 0.0, 1.0))
    c_max = ((1.0 - mix) * base_c + mix * peak).astype(np.float32)
    c_max = c_max / c_max.sum() if c_max.sum() > 0 else base_c

    target = float(c_max.var())
    cvar = np.clip(
        rng.normal(target, target * 0.1 + 1e-6, size=T),
        0.0, None,
    ).astype(np.float32)

    K_mean = float(max(K, 1))
    fp_meta = {"source": "synthetic"}
    fp_meta.update(meta or {})

    return Fingerprint(
        mean_injection_trace=E,
        global_burstiness=beta,
        max_centrality=c_max,
        mean_components=K_mean,
        T=T,
        neuron_count=int(neuron_count),
        state_size_mb=float(state_size_mb),
        complexity_ratio=float(complexity_ratio),
        compute_sequence=np.zeros(T, dtype=np.float32),
        centrality_var=cvar,
        sample_measured_injection_trace=E.copy(),
        sample_index=-1,
        sample_label=-1,
        sample_path=f"synthetic/{fp_meta.get('source', 'synthetic')}",
        meta=fp_meta,
    )
