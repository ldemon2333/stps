"""Phase-shifting kernel for STPS Stage 2 (paper Algorithm 1).

Finds the offset Δt minimising max_t (E_m[t] + E_new[t-Δt]) under a hard
bandwidth ceiling BW_max. Falls back to the min-peak offset when no offset is
feasible so the caller can decide whether to reject the task.
"""
from __future__ import annotations

import math
from typing import Tuple

import numpy as np


def _shift(E_new: np.ndarray, dt: int, horizon: int) -> np.ndarray:
    out = np.zeros(horizon, dtype=np.float32)
    end = min(horizon, dt + E_new.shape[0])
    if end > dt:
        out[dt:end] = E_new[: end - dt]
    return out


def find_optimal_offset(
    E_m: np.ndarray,
    E_new: np.ndarray,
    D_max: int,
    BW_max: float,
) -> Tuple[int, float]:
    """Return ``(offset, peak)`` minimising the combined transient peak.

    Args:
        E_m: (H,) forecasted background traffic on the candidate card.
        E_new: (T_new,) traffic timeline of the incoming task.
        D_max: maximum tolerable QoS delay in ticks.
        BW_max: hard bandwidth ceiling.

    Returns:
        Tuple of (best offset Δt*, resulting peak). If no offset stays under
        BW_max, the offset is the one minimising peak and the returned peak
        will exceed BW_max so the scheduler can reject the task.
    """
    horizon = int(E_m.shape[0])
    D_max = int(max(D_max, 0))
    best_offset = -1
    best_peak = math.inf

    for dt in range(0, D_max + 1):
        shifted = _shift(E_new, dt, horizon)
        combined = E_m + shifted
        peak = float(combined.max(initial=0.0))
        if peak <= BW_max and peak < best_peak:
            best_peak = peak
            best_offset = dt

    if best_offset != -1:
        return best_offset, best_peak

    # No feasible offset under BW_max -- return min-peak fallback.
    fallback_offset = 0
    fallback_peak = math.inf
    for dt in range(0, D_max + 1):
        shifted = _shift(E_new, dt, horizon)
        peak = float((E_m + shifted).max(initial=0.0))
        if peak < fallback_peak:
            fallback_peak = peak
            fallback_offset = dt
    return fallback_offset, float(fallback_peak)
