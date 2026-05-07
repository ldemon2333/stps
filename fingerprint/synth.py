"""Synthetic fingerprint generator (development workflows, no torch).

Produces a Fingerprint matching the new §7 schema with target (β, K̄, var).
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
    seed: Optional[int] = None,
    meta: Optional[Dict[str, str]] = None,
) -> "Fingerprint":
    """Build a Fingerprint with the requested high-level statistics."""
    from . import Fingerprint

    rng = np.random.default_rng(seed)
    T = max(int(T), 1)
    V = max(int(V), 1)

    base = 1.0
    pulse_idx = int(rng.integers(0, T))
    pulse = max(beta_target * base * T - base * T, 0.0)
    E = np.full(T, base, dtype=np.float32)
    if T > 1:
        spread = max(T // 16, 1)
        for k in range(-spread, spread + 1):
            i = pulse_idx + k
            if 0 <= i < T:
                E[i] += pulse / (2 * spread + 1)
    else:
        E[0] += pulse
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
        traffic_sequence=E,
        global_burstiness=beta,
        max_centrality=c_max,
        mean_components=K_mean,
        T=T,
        neuron_count=int(neuron_count),
        state_size_mb=float(state_size_mb),
        complexity_ratio=float(complexity_ratio),
        compute_sequence=np.zeros(T, dtype=np.float32),
        centrality_var=cvar,
        meta=fp_meta,
    )
