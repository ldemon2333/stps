from __future__ import annotations

import numpy as np
import pytest

from fingerprint import (
    extract_fingerprint_from_W,
    load_fingerprint,
    mask_conv2d,
    mask_identity,
    mask_linear,
    mask_pruned,
    save_fingerprint,
    split_layer,
)
from fingerprint.edge_builder import EdgeSpec, HaloEdgeSpec, build_edge_tensor


def test_sparsity_masks_match_documented_closed_forms():
    assert mask_linear(7).value == pytest.approx(7.0)
    assert mask_linear(7).case == "A"

    assert mask_conv2d(K=3, C_out_shard=4).value == pytest.approx(36.0)
    assert mask_conv2d(K=3, C_out_shard=4).case == "B"

    assert mask_pruned(v_j_size=10, density=0.25).value == pytest.approx(2.5)
    assert mask_pruned(v_j_size=10, density=0.25).case == "C"
    assert mask_pruned(v_j_size=10, density=2.0).value == pytest.approx(10.0)
    assert mask_pruned(v_j_size=10, density=-1.0).value == pytest.approx(0.0)

    assert mask_identity().value == pytest.approx(1.0)
    assert mask_identity().case == "I"


def test_edge_tensor_uses_batch_mean_not_batch_sum_and_builds_compute_channel():
    src, dst = (
        split_layer("src_lif", "vec", (2,), N_core_cap=1024)[0],
        split_layer("dst_lif", "vec", (3,), N_core_cap=1024)[0],
    )
    trace = np.array(
        [
            [[1, 0], [1, 1]],
            [[0, 0], [1, 0]],
        ],
        dtype=np.float32,
    )

    W = build_edge_tensor(
        [src, dst],
        [
            EdgeSpec(
                src="src_lif",
                dst="dst_lif",
                kind="linear",
                mask_factory=lambda _s, d: mask_linear(d.size),
                compute_per_flit=8.0,
            )
        ],
        {"src_lif": trace},
        T=2,
    )

    assert W.shape == (2, 2, 2, 2)
    np.testing.assert_allclose(W[:, 0, 1, 0], np.array([4.5, 1.5], dtype=np.float32))
    np.testing.assert_allclose(W[:, 0, 1, 1], np.array([36.0, 12.0], dtype=np.float32))


def test_edge_tensor_respects_link_delay_with_zero_padding():
    src, dst = (
        split_layer("src_lif", "vec", (1,), N_core_cap=1024)[0],
        split_layer("dst_lif", "vec", (2,), N_core_cap=1024)[0],
    )
    trace = np.array([[[1]], [[0]], [[1]]], dtype=np.float32)

    W = build_edge_tensor(
        [src, dst],
        [
            EdgeSpec(
                src="src_lif",
                dst="dst_lif",
                kind="linear",
                mask_factory=lambda _s, d: mask_linear(d.size),
                delta=1,
            )
        ],
        {"src_lif": trace},
        T=3,
    )

    np.testing.assert_allclose(W[:, 0, 1, 0], np.array([0.0, 2.0, 0.0], dtype=np.float32))


def test_halo_edges_are_geometry_scaled_memory_traffic_without_compute():
    pops = split_layer("stem_lif", "fmap", (1, 56, 56), N_core_cap=1024, K=3)
    halo_edges = []
    for pop in pops:
        for sibling_id, flits in pop.halo_neighbors:
            halo_edges.append(
                HaloEdgeSpec(
                    src_global_idx=pop.shard_id,
                    dst_global_idx=sibling_id,
                    flits_per_step=flits,
                )
            )
    trace = np.ones((1, 1, sum(pop.size for pop in pops)), dtype=np.float32)

    W = build_edge_tensor(pops, [], {"stem_lif": trace}, T=1, halo_edges=halo_edges)

    assert W[0, 0, 1, 0] == pytest.approx(112.0)
    assert W[0, 1, 0, 0] == pytest.approx(112.0)
    assert W[0, 1, 2, 0] == pytest.approx(112.0)
    assert float(W[..., 1].sum()) == pytest.approx(0.0)


def test_extractor_sums_traffic_compute_filters_components_and_keeps_max_centrality():
    W = np.zeros((3, 4, 4, 2), dtype=np.float32)
    W[0, 0, 1, 0] = 2.0
    W[0, 2, 3, 0] = 0.25
    W[0, 0, 1, 1] = 20.0
    W[1, 0, 1, 0] = 4.0
    W[1, 1, 2, 0] = 4.0
    W[1, 1, 2, 1] = 40.0
    W[2, 3, 2, 0] = 1.0

    fp = extract_fingerprint_from_W(
        W,
        neuron_count=4,
        state_size_mb=1.0,
        complexity_ratio=2.0,
        edge_threshold=1.0,
        meta={"case": "contract"},
    )

    np.testing.assert_allclose(fp.traffic_sequence, np.array([2.25, 8.0, 1.0], dtype=np.float32))
    np.testing.assert_allclose(fp.compute_sequence, np.array([20.0, 40.0, 0.0], dtype=np.float32))
    assert fp.global_burstiness == pytest.approx(8.0 / ((2.25 + 8.0 + 1.0) / 3.0))
    assert fp.mean_components == pytest.approx(2.0 / 3.0)
    assert int(np.argmax(fp.max_centrality)) in {1, 2}
    assert fp.T == 3
    assert fp.complexity_ratio == pytest.approx(2.0)
    assert fp.meta == {"case": "contract"}


def test_extractor_handles_all_zero_and_empty_windows():
    zero = extract_fingerprint_from_W(
        np.zeros((2, 3, 3), dtype=np.float32), neuron_count=3, state_size_mb=1.0
    )
    np.testing.assert_allclose(zero.traffic_sequence, np.zeros(2, dtype=np.float32))
    np.testing.assert_allclose(zero.max_centrality, np.full(3, 1.0 / 3.0, dtype=np.float32))
    assert zero.global_burstiness == pytest.approx(1.0)
    assert zero.mean_components == pytest.approx(0.0)

    empty = extract_fingerprint_from_W(
        np.zeros((0, 3, 3), dtype=np.float32), neuron_count=3, state_size_mb=1.0
    )
    assert empty.T == 0
    assert empty.traffic_sequence.shape == (0,)
    assert empty.max_centrality.shape == (3,)
    assert empty.mean_components == pytest.approx(0.0)


def test_save_load_save_round_trip_preserves_schema_values(tmp_path):
    W = np.zeros((2, 2, 2, 2), dtype=np.float32)
    W[:, 0, 1, 0] = [1.0, 3.0]
    W[:, 0, 1, 1] = [2.0, 6.0]
    fp = extract_fingerprint_from_W(
        W, neuron_count=2, state_size_mb=4.0, meta={"model": "tiny"}
    )

    path1 = tmp_path / "first.npz"
    path2 = tmp_path / "second.npz"
    save_fingerprint(path1, fp)
    loaded = load_fingerprint(path1)
    save_fingerprint(path2, loaded)
    loaded_again = load_fingerprint(path2)

    np.testing.assert_array_equal(loaded_again.traffic_sequence, fp.traffic_sequence)
    np.testing.assert_array_equal(loaded_again.compute_sequence, fp.compute_sequence)
    np.testing.assert_array_equal(loaded_again.max_centrality, fp.max_centrality)
    assert loaded_again.global_burstiness == pytest.approx(fp.global_burstiness)
    assert loaded_again.mean_components == pytest.approx(fp.mean_components)
    assert loaded_again.meta == fp.meta
