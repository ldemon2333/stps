"""Hotspot splitting helper for STPS Stage 3 (paper §4.3).

Populations whose in-eigenvector centrality exceeds a threshold are flagged
for splitting across multiple PIM cores. v1 only records indices; physical
placement still defers to the underlying card model.
"""
from __future__ import annotations

from typing import List

import numpy as np


def split_population(c_last: np.ndarray, threshold: float) -> List[int]:
    """Return indices of populations to split based on centrality.

    Args:
        c_last: (V,) last-step centrality vector from the fingerprint.
        threshold: fractional centrality above which a population is a hotspot.
    """
    if c_last.size == 0:
        return []
    return [int(i) for i, v in enumerate(c_last) if float(v) >= threshold]
