"""Hardware-aware micro-population slicing (docs/fingerprint.md §2.3).

Slicing target is a LIF *node* v_i defined by its output tensor shape
(NOT the upstream Conv2d/Linear operator). This module decomposes a
node into a list of MicroPopulations satisfying |v_i| ≤ N_core_cap.

Three node kinds:
    "vec"          — 1D output (C,)               §2.3.1
    "fmap"         — 4D output (C, H, W)          §2.3.2  (may emit halo edges)
    "token_embed"  — 2D output (N_tok, C)         §2.3.3
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple


@dataclass
class MicroPopulation:
    """One slice of a LIF node, sized to fit a single CIM core.

    Attributes:
        node_id: name of the parent LIF node (e.g. "proj_lif").
        shard_id: index within the parent node's slice list.
        size: |v_i| — number of neurons in this micro-population.
        kind: "vec" | "fmap" | "token_embed".
        meta: per-shard metadata (channel range, spatial range, head id, ...).
        halo_neighbors: list of (sibling shard_id, flits_per_step) tuples for
            halo edges that must be registered later. Only fmap kind populates
            this when single-channel feature map exceeds N_core_cap (§2.3.2).
    """

    node_id: str
    shard_id: int
    size: int
    kind: str
    meta: dict = field(default_factory=dict)
    halo_neighbors: List[Tuple[int, int]] = field(default_factory=list)


def split_layer(
    node_id: str,
    kind: str,
    shape_meta: tuple,
    N_core_cap: int = 4096,
    K: int = 3,
    head_dim: int = 32,
) -> List[MicroPopulation]:
    """Slice a LIF node's output tensor into core-cap-bounded shards.

    Args:
        node_id: name of the LIF node being sliced.
        kind: "vec" | "fmap" | "token_embed".
        shape_meta: shape descriptor:
            "vec"          → (C,)
            "fmap"         → (C, H, W)
            "token_embed"  → (N_tok, C)
        N_core_cap: per-core neuron capacity (Darwin v3 default 4096).
        K: kernel size of the Conv2d driving this fmap node (only used to
            scale halo flits when single-channel S > N_core_cap).
        head_dim: attention head dimension (token_embed only).

    Returns:
        List of MicroPopulations.
    """
    if kind == "vec":
        return _split_vec(node_id, shape_meta, N_core_cap)
    if kind == "fmap":
        return _split_fmap(node_id, shape_meta, N_core_cap, K)
    if kind == "token_embed":
        return _split_token_embed(node_id, shape_meta, N_core_cap, head_dim)
    raise ValueError(f"Unknown LIF-node kind: {kind!r}")


def _split_vec(node_id: str, shape_meta: tuple, N_core_cap: int) -> List[MicroPopulation]:
    (C,) = shape_meta
    P = max(1, math.ceil(C / N_core_cap))
    pops: List[MicroPopulation] = []
    for p in range(P):
        lo = p * N_core_cap
        hi = min(lo + N_core_cap, C)
        pops.append(
            MicroPopulation(
                node_id=node_id, shard_id=p, size=hi - lo, kind="vec",
                meta={"axis": "out_ch", "range": (lo, hi)},
            )
        )
    return pops


def _split_fmap(
    node_id: str, shape_meta: tuple, N_core_cap: int, K: int,
) -> List[MicroPopulation]:
    C, H, W = shape_meta
    S = H * W
    pops: List[MicroPopulation] = []

    if S <= N_core_cap:
        # §2.3.2 (i): channel-only split; one core holds Cp full feature maps.
        Cp = max(1, N_core_cap // S)
        n_shards = math.ceil(C / Cp)
        for p in range(n_shards):
            c_lo = p * Cp
            c_hi = min(c_lo + Cp, C)
            pops.append(
                MicroPopulation(
                    node_id=node_id, shard_id=p, size=(c_hi - c_lo) * S,
                    kind="fmap",
                    meta={"axis": "channel", "c_range": (c_lo, c_hi),
                          "h_range": (0, H), "w_range": (0, W)},
                )
            )
        return pops

    # §2.3.2 (ii): single feature map exceeds core cap → channel+row split.
    R = max(1, N_core_cap // W)  # rows per strip
    halo_flits = (K - 1) * W  # per-step halo flits, Cin folded later by edge_builder
    shard_id = 0
    for c in range(C):
        prev_in_channel: Optional[int] = None
        n_strips = math.ceil(H / R)
        for r in range(n_strips):
            h_lo = r * R
            h_hi = min(h_lo + R, H)
            pops.append(
                MicroPopulation(
                    node_id=node_id, shard_id=shard_id,
                    size=(h_hi - h_lo) * W, kind="fmap",
                    meta={"axis": "channel+row", "c_range": (c, c + 1),
                          "h_range": (h_lo, h_hi), "w_range": (0, W)},
                )
            )
            if prev_in_channel is not None:
                # Bidirectional halo edges between adjacent strips of same channel.
                pops[-1].halo_neighbors.append((prev_in_channel, halo_flits))
                pops[prev_in_channel].halo_neighbors.append((shard_id, halo_flits))
            prev_in_channel = shard_id
            shard_id += 1
    return pops


def _split_token_embed(
    node_id: str, shape_meta: tuple, N_core_cap: int, head_dim: int,
) -> List[MicroPopulation]:
    N_tok, C = shape_meta
    # Per §2.3.3: same head's d_head channels must not split across cores.
    raw_cap = max(1, N_core_cap // max(N_tok, 1))
    Cp = max(head_dim, (raw_cap // head_dim) * head_dim)
    n_shards = math.ceil(C / Cp)
    pops: List[MicroPopulation] = []
    for p in range(n_shards):
        c_lo = p * Cp
        c_hi = min(c_lo + Cp, C)
        pops.append(
            MicroPopulation(
                node_id=node_id, shard_id=p, size=N_tok * (c_hi - c_lo),
                kind="token_embed",
                meta={"axis": "embed", "n_tok": N_tok,
                      "c_range": (c_lo, c_hi), "head_dim": head_dim},
            )
        )
    return pops
