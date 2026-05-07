"""Synaptic connectivity mask M_ij (docs/fingerprint.md §4).

M_ij is the per-spike physical multicast factor on edge i→j: how many
flits a single source spike from v_i incurs at v_j. Three closed-form
cases (offline O(1) lookup, no extra training):

    Case A (Linear / Fully-connected):  M_ij = |v_j|
    Case B (Conv2d, kernel K×K):        M_ij = K^2 * C_out_shard
    Case C (Pruned / sparse, density 1-ρ): M_ij = |v_j| * (1 - ρ)
    Identity (residual add, halo edge):  M_ij = 1
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class SparsityMask:
    """Closed-form value of M_ij with provenance for debugging."""

    value: float
    case: Literal["A", "B", "C", "I"]
    why: str

    def __float__(self) -> float:
        return float(self.value)


def mask_linear(v_j_size: int) -> SparsityMask:
    """Case A: fully connected Linear edge — every output unit receives one spike."""
    return SparsityMask(
        value=float(v_j_size), case="A",
        why=f"Linear: M_ij = |v_j| = {v_j_size}",
    )


def mask_conv2d(K: int, C_out_shard: int) -> SparsityMask:
    """Case B: Conv2d kernel K×K; per source spike, K^2 * C_out flits land at j.

    C_out_shard is the *destination shard's* channel count, NOT total Cout —
    the mask is local to one (i, j) edge in the sliced graph.
    """
    val = float(K * K * C_out_shard)
    return SparsityMask(
        value=val, case="B",
        why=f"Conv2d K={K} C_out_shard={C_out_shard}: M_ij = K^2*C_out = {val:.0f}",
    )


def mask_pruned(v_j_size: int, density: float) -> SparsityMask:
    """Case C: structured pruning — only `density` fraction of synapses survive.

    density ∈ [0, 1] is (1 - ρ) where ρ is sparsity ratio.
    """
    d = max(0.0, min(1.0, float(density)))
    val = float(v_j_size) * d
    return SparsityMask(
        value=val, case="C",
        why=f"Pruned density={d:.3f}: M_ij = |v_j|*(1-rho) = {val:.2f}",
    )


def mask_identity() -> SparsityMask:
    """Identity edge — residual add or intra-node halo: one spike → one flit."""
    return SparsityMask(value=1.0, case="I", why="Identity edge: M_ij = 1")
