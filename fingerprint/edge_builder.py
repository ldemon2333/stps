"""DTDG edge builder (docs/fingerprint.md §3, §4, §Step 3, §Step 4).

Given:
    - a list of LIF nodes, each already sliced into MicroPopulations (§2.3),
    - a topology spec describing edges (kind ∈ {linear, conv2d, identity}) and
      Sparsity Mask M_ij case,
    - per-LIF spike traces of shape (T, B, U) collected by forward hooks,

build the dynamic 2D edge tensor

    W ∈ R^{T × V' × V' × 2}

where channel 0 is Traffic^(t)_ij (NoC flits) and channel 1 is
Compute^(t)_ij (downstream PIM SOPs). Both quantities are per-sample
expectations (mean over B), per §Step 4.

Spike traces are ndarrays of shape (T, B, U_i) with U_i = sum of |v_i^(p)|
over all shards of node i. We split U_i along the same axis the slicer used
so spike-rate ↔ shard alignment is automatic.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from .mask import SparsityMask, mask_identity
from .slicing import MicroPopulation


@dataclass
class EdgeSpec:
    """One logical edge between two LIF nodes (pre-slicing).

    Attributes:
        src: source LIF node id (must match MicroPopulation.node_id of some shard).
        dst: destination LIF node id.
        kind: "linear" | "conv2d" | "identity".
        mask_factory: callable (src_pop, dst_pop) -> SparsityMask. Closure
            over kernel size / C_out etc., evaluated per shard pair.
        compute_per_flit: how many SOPs the destination triggers per arriving
            flit (paper §3.3). Default 1 (one MAC per spike on PIM array).
        delta: link delay in ticks (§3.2 δ_ij). Default 0.
    """

    src: str
    dst: str
    kind: str
    mask_factory: callable
    compute_per_flit: float = 1.0
    delta: int = 0


@dataclass
class HaloEdgeSpec:
    """Intra-node halo edge between two sibling shards (§2.3.2 case ii)."""

    src_global_idx: int
    dst_global_idx: int
    flits_per_step: int


def build_edge_tensor(
    pops: Sequence[MicroPopulation],
    edges: Sequence[EdgeSpec],
    spike_traces: Dict[str, np.ndarray],
    T: int,
    halo_edges: Optional[Sequence[HaloEdgeSpec]] = None,
    shard_offsets: Optional[Dict[str, List[Tuple[int, int]]]] = None,
) -> np.ndarray:
    """Compute W ∈ R^{T × V' × V' × 2}.

    Args:
        pops: flat list of all MicroPopulations (length V'). Order is the
            graph node ordering used everywhere downstream.
        edges: logical inter-node edges (Cartesian-expanded over shard pairs).
        spike_traces: dict node_id -> (T, B, U_i) ndarray. U_i must equal the
            sum of `size` over all shards of node_id.
        T: number of ticks.
        halo_edges: optional flat list of halo edges between sibling shards.
            Already references global indices into `pops`.
        shard_offsets: optional precomputed dict node_id -> [(unit_lo, unit_hi)]
            slicing the U_i dimension of spike traces per shard. If None,
            inferred from MicroPopulation.size (cumulative).

    Returns:
        W: (T, V', V', 2) float32. W[..., 0] = Traffic, W[..., 1] = Compute.
    """
    Vp = len(pops)
    W = np.zeros((T, Vp, Vp, 2), dtype=np.float32)
    if Vp == 0:
        return W

    pop_index: Dict[Tuple[str, int], int] = {
        (p.node_id, p.shard_id): i for i, p in enumerate(pops)
    }
    shards_by_node: Dict[str, List[int]] = {}
    for i, p in enumerate(pops):
        shards_by_node.setdefault(p.node_id, []).append(i)

    if shard_offsets is None:
        shard_offsets = _infer_shard_offsets(pops)

    # Per-shard, per-tick mean spike count (over B): r_i^(t) = E_b[ sum over units in shard ]
    # shape (T, n_shards_of_node)
    rates: Dict[str, np.ndarray] = {}
    for node_id, trace in spike_traces.items():
        offs = shard_offsets[node_id]
        # trace: (T, B, U). mean over B -> (T, U). Then shard-wise sum.
        if trace.ndim != 3:
            raise ValueError(
                f"spike_traces[{node_id!r}] must be (T,B,U); got {trace.shape}"
            )
        if trace.shape[0] != T:
            raise ValueError(
                f"spike_traces[{node_id!r}] T={trace.shape[0]} != expected {T}"
            )
        per_unit = trace.astype(np.float32).mean(axis=1)  # (T, U)
        n_shards = len(offs)
        per_shard = np.zeros((T, n_shards), dtype=np.float32)
        for s_idx, (lo, hi) in enumerate(offs):
            if hi > lo:
                per_shard[:, s_idx] = per_unit[:, lo:hi].sum(axis=1)
        rates[node_id] = per_shard

    # Inter-node edges: Cartesian-expand each logical edge across shards.
    for e in edges:
        if e.src not in shards_by_node or e.dst not in shards_by_node:
            continue
        src_shards = shards_by_node[e.src]
        dst_shards = shards_by_node[e.dst]
        src_rate = rates.get(e.src)
        if src_rate is None:
            continue
        for s_local, src_idx in enumerate(src_shards):
            for d_local, dst_idx in enumerate(dst_shards):
                src_pop = pops[src_idx]
                dst_pop = pops[dst_idx]
                if not _shards_connected(src_pop, dst_pop, e.kind):
                    continue
                m: SparsityMask = e.mask_factory(src_pop, dst_pop)
                m_val = float(m.value)
                if m_val <= 0.0:
                    continue
                # Traffic^(t) = E_b[ x_spike * |v_i| ] * M_ij  with delay δ.
                # spike trace already accumulates spikes per shard; that equals
                # rate (per sample) directly — no need to multiply by |v_i|
                # again because src_rate counts spike events. But the doc
                # writes (x · |v_i|) * M; here x is the binary vector and
                # x_spike · |v_i| is per-shard spike count, which = src_rate.
                src_series = src_rate[:, s_local]
                if e.delta != 0:
                    src_series = _shift(src_series, e.delta)
                W[:, src_idx, dst_idx, 0] += src_series * m_val
                W[:, src_idx, dst_idx, 1] += src_series * m_val * float(e.compute_per_flit)

    # Halo edges: bidirectional, identity mask, no compute (memory copy only).
    if halo_edges:
        for h in halo_edges:
            src_pop = pops[h.src_global_idx]
            # Use the source shard's local rate.
            offs_src = shard_offsets[src_pop.node_id]
            local_pos = next(
                i for i, idx in enumerate(shards_by_node[src_pop.node_id])
                if idx == h.src_global_idx
            )
            _ = offs_src  # only used for shape sanity
            src_rate = rates.get(src_pop.node_id)
            if src_rate is None:
                continue
            flits = float(h.flits_per_step)
            # identity multicast: 1 flit per boundary spike, but boundary
            # spike rate ≈ (halo_thickness * W * Cin) flits. Trace already
            # accounts for spike count; halo flits/step is a fixed constant
            # added regardless of rate fluctuations? In v3 we follow the
            # doc literally: Traffic_halo ≈ (K-1)*W*Cin per *step* (constant
            # geometry term), modulated by the source firing rate fraction.
            rate = src_rate[:, local_pos]
            # Normalize: per-unit firing prob ≈ rate / |v_i|
            v_size = max(1, src_pop.size)
            firing_prob = rate / float(v_size)
            W[:, h.src_global_idx, h.dst_global_idx, 0] += firing_prob * flits
            # Compute channel: halo is pure memory copy → 0 SOPs.

    return W


def _shards_connected(src_pop: MicroPopulation, dst_pop: MicroPopulation, kind: str) -> bool:
    """Restrict Cartesian product based on edge kind.

    For Linear / identity edges every (i,j) shard pair is connected (full fan-out).
    For Conv2d edges the destination shard's input channel range must overlap
    the source shard's channel range — but since downstream Conv2d sees the
    *whole* upstream feature map as input (channels are summed inside the conv),
    every dst shard depends on every src shard's channel. So Conv2d is also
    fully connected at the shard level. We keep the hook for future depthwise /
    grouped conv extensions.
    """
    if kind in ("linear", "identity", "conv2d"):
        return True
    return False


def _shift(series: np.ndarray, delta: int) -> np.ndarray:
    """Causal shift by delta ticks (zero-pad on the left)."""
    if delta <= 0:
        return series
    out = np.zeros_like(series)
    if delta < series.shape[0]:
        out[delta:] = series[:-delta]
    return out


def _infer_shard_offsets(pops: Sequence[MicroPopulation]) -> Dict[str, List[Tuple[int, int]]]:
    """Build cumulative (unit_lo, unit_hi) ranges from MicroPopulation.size.

    Caller is responsible for keeping the in-trace flatten order matching
    the slicer's shard order. The default order is just whatever the slicer
    produced, which for split_layer matches contiguous channel ranges.
    """
    offsets: Dict[str, List[Tuple[int, int]]] = {}
    for p in pops:
        cur = offsets.setdefault(p.node_id, [])
        lo = cur[-1][1] if cur else 0
        cur.append((lo, lo + p.size))
    return offsets


__all__ = [
    "EdgeSpec",
    "HaloEdgeSpec",
    "build_edge_tensor",
]
