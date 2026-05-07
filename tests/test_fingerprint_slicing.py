from __future__ import annotations

import pytest

from fingerprint import split_layer


def test_vec_node_splits_by_output_channel_capacity():
    pops = split_layer("fc1_lif", "vec", (1536,), N_core_cap=1024)

    assert len(pops) == 2
    assert [p.size for p in pops] == [1024, 512]
    assert [p.meta["range"] for p in pops] == [(0, 1024), (1024, 1536)]
    assert all(p.kind == "vec" for p in pops)


@pytest.mark.parametrize(
    ("node_id", "shape", "expected_shards", "expected_cp"),
    [
        ("proj_lif", (48, 32, 32), 48, 1),
        ("proj_lif3", (384, 8, 8), 24, 16),
    ],
)
def test_fmap_node_splits_channel_first_without_halo_when_spatial_fits(
    node_id, shape, expected_shards, expected_cp
):
    pops = split_layer(node_id, "fmap", shape, N_core_cap=1024, K=3)

    assert len(pops) == expected_shards
    assert all(p.kind == "fmap" for p in pops)
    assert all(p.meta["axis"] == "channel" for p in pops)
    assert all(p.halo_neighbors == [] for p in pops)

    first_c_lo, first_c_hi = pops[0].meta["c_range"]
    assert first_c_hi - first_c_lo == expected_cp
    assert max(p.size for p in pops) <= 1024


def test_fmap_node_adds_bidirectional_halo_edges_when_single_channel_exceeds_capacity():
    pops = split_layer("stem_lif", "fmap", (1, 56, 56), N_core_cap=1024, K=3)

    assert len(pops) == 4
    assert [p.meta["h_range"] for p in pops] == [(0, 18), (18, 36), (36, 54), (54, 56)]
    assert [p.size for p in pops] == [1008, 1008, 1008, 112]
    assert pops[0].halo_neighbors == [(1, 112)]
    assert pops[1].halo_neighbors == [(0, 112), (2, 112)]
    assert pops[2].halo_neighbors == [(1, 112), (3, 112)]
    assert pops[3].halo_neighbors == [(2, 112)]


def test_token_embed_attn_lif_uses_strict_head_aligned_cp_formula():
    pops = split_layer(
        "attn_lif", "token_embed", (64, 384), N_core_cap=1024, head_dim=32
    )

    assert len(pops) == 12
    assert [p.meta["c_range"] for p in pops] == [
        (0, 32),
        (32, 64),
        (64, 96),
        (96, 128),
        (128, 160),
        (160, 192),
        (192, 224),
        (224, 256),
        (256, 288),
        (288, 320),
        (320, 352),
        (352, 384),
    ]
    assert all(p.size == 64 * 32 for p in pops)
    assert all(p.meta["head_dim"] == 32 for p in pops)
