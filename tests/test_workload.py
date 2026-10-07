import json

import numpy as np
import pytest

from fingerprint.workload import Workload, from_edge_tensor, load_workload, make_sparse_workload, save_workload


def minimal(**changes):
    values = dict(pop_size=[4, 8], edge_src=[0], edge_dst=[1],
                  edge_expected_flits=[[0.5], [0.5]], compute_sops=[[1, 0], [0, 3]],
                  state_size_mb=1.5, source="fixture:explicit", metadata={"sample": "a"})
    values.update(changes)
    return Workload(**values)


def test_quantization_carries_across_ticks_and_cannot_be_mutated():
    w = minimal(edge_expected_flits=[[0.1]] * 10, compute_sops=[[0, 0]] * 10)
    assert w.flits[:, 0].tolist() == [0] * 9 + [1]
    with pytest.raises(ValueError):
        w.flits.flags.writeable = True
    assert w.totals["quantized_flits"] == 1


@pytest.mark.parametrize("suffix", [".json", ".npz"])
def test_roundtrip(tmp_path, suffix):
    w = minimal()
    restored = load_workload(save_workload(tmp_path / f"trace{suffix}", w))
    for field in ("edge_expected_flits", "compute_sops", "pop_size", "flits"):
        np.testing.assert_array_equal(getattr(w, field), getattr(restored, field))
    assert restored.source == w.source and restored.metadata == w.metadata


def test_zero_edges_and_self_loop_compute():
    assert minimal(edge_src=[], edge_dst=[], edge_expected_flits=[[], []]).flits.shape == (2, 0)
    tensor = np.zeros((2, 2, 2, 2))
    tensor[0, 0, 0] = [2, 7]
    tensor[0, 1, 0, 1] = 3
    w = from_edge_tensor(tensor, [4, 8], 0, "fixture:tensor")
    assert w.edge_src.tolist() == w.edge_dst.tolist() == [0]
    assert w.compute_sops.tolist() == [[10, 0], [0, 0]]


@pytest.mark.parametrize("changes", [
    {"pop_size": [True, 8]}, {"edge_src": [0.0]}, {"edge_dst": [2]},
    {"compute_sops": [[1, 2]]}, {"edge_expected_flits": [[float("nan")], [0]]},
    {"edge_expected_flits": [[float(2**63)], [0]]}, {"source": " "},
    {"state_size_mb": -1}, {"metadata": {"bad": float("inf")}},
    {"edge_src": [0, 0], "edge_dst": [1, 1], "edge_expected_flits": [[1, 1], [0, 0]]},
])
def test_invalid(changes):
    with pytest.raises(ValueError):
        minimal(**changes)


def test_missing_compute_legacy_wrong_units(tmp_path):
    path = tmp_path / "trace.json"
    path.write_text('{"E": [1,2]}')
    with pytest.raises(ValueError, match="legacy"):
        load_workload(path)
    save_workload(path, minimal())
    doc = json.loads(path.read_text())
    doc.pop("compute_sops")
    path.write_text(json.dumps(doc))
    with pytest.raises(ValueError, match="compute_sops"):
        load_workload(path)
    save_workload(path, minimal())
    doc = json.loads(path.read_text())
    doc["units"]["traffic"] = "packets"
    path.write_text(json.dumps(doc))
    with pytest.raises(ValueError, match="units"):
        load_workload(path)


def test_synthetic_repeatable_and_sparse():
    a, b = make_sparse_workload(seed=17), make_sparse_workload(seed=17)
    np.testing.assert_array_equal(a.flits, b.flits)
    np.testing.assert_array_equal(a.compute_sops, b.compute_sops)
    assert not a.flits[0].any()
    assert a.flits.sum() > 0 and a.compute_sops.sum() > 0
    assert (a.edge_src == a.edge_dst).any()
    assert a.source.startswith("synthetic:")
@pytest.mark.parametrize("compute", [True, False])
def test_nonfinite_aggregate_rejected(compute):
    with pytest.raises(ValueError, match="total exceeds"):
        Workload([1, 1], [0, 1], [1, 0],
                 [[0, 0]] if compute else [[1e308, 1e308]],
                 [[1e308, 1e308]] if compute else [[0, 0]], 0, "test")
