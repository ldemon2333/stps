from dataclasses import fields
import json

import numpy as np
import pytest

from fingerprint.scheduling import (
    ARRAY_FIELDS,
    SchedulingFingerprint,
    build_fingerprint,
    load_fingerprint,
    save_fingerprint,
)
from fingerprint.workload import Workload, load_workload, save_workload


def sample(**changes):
    values = dict(
        pop_size=[4, 8], edge_src=[0, 1], edge_dst=[1, 1],
        edge_expected_flits=[[0.5, 3], [0.5, 1]], compute_sops=[[1, 3], [2, 6]],
        state_size_mb=1.5, source="calibration:a",
    )
    values.update(changes)
    return Workload(**values)


def profile(**changes):
    values = dict(
        pop_size=[4, 8], edge_src=[0, 1], edge_dst=[1, 1],
        expected_edge_flits=[[0, 3], [1, 1]], compute_total_sops=[4, 8],
        state_size_mb=1.5, source="offline:test",
    )
    values.update(changes)
    return SchedulingFingerprint(**values)


def test_single_sample_quantizes_before_build_and_excludes_local_load():
    calibration = sample()
    result = build_fingerprint([calibration], source="offline:one")
    np.testing.assert_array_equal(result.expected_edge_flits, calibration.flits)
    assert result.expected_edge_flits[:, 0].tolist() == [0, 1]
    assert result.compute_total_sops.tolist() == [4, 8]
    assert result.T == 2 and result.population_count == 2
    assert result.total_compute_sops == 12 and result.mean_compute_sops == 6
    assert result.total_noc_endpoint == 2 and result.mean_noc_endpoint == 1
    assert result.metadata == {
        "calibration_sample_sources": ["calibration:a"], "sample_count": 1,
        "quantization": "mean_of_per_sample_cumulative_floor_flits",
    }


def test_samples_average_quantized_flits_without_requantizing_prediction():
    a = sample(edge_expected_flits=[[0.5, 0], [0.5, 0]])
    b = sample(edge_expected_flits=[[1, 0], [0, 0]], compute_sops=[[0, 2], [0, 4]],
               source="calibration:b")
    result = build_fingerprint([a, b], "offline:two", {"dataset_split": "calibration"})
    assert result.expected_edge_flits.tolist() == [[0.5, 0], [0.5, 0]]
    assert result.compute_total_sops.tolist() == [3, 6]
    assert result.total_noc_endpoint == 2
    assert result.metadata["calibration_sample_sources"] == ["calibration:a", "calibration:b"]
    assert result.metadata["dataset_split"] == "calibration"


@pytest.mark.parametrize("changes", [
    {"pop_size": [4, 9]}, {"edge_dst": [0, 1]}, {"state_size_mb": 2},
    {"edge_src": [1, 0], "edge_dst": [1, 1]},
    {"edge_expected_flits": [[0, 0]], "compute_sops": [[0, 0]]},
])
def test_reject_incompatible_calibration_samples(changes):
    with pytest.raises(ValueError, match="share static graph"):
        build_fingerprint([sample(), sample(**changes)], "offline:bad")


@pytest.mark.parametrize("samples", [[], None, ["not-a-workload"]])
def test_requires_explicit_nonempty_calibration_samples(samples):
    with pytest.raises(ValueError, match="nonempty sequence"):
        build_fingerprint(samples, "offline:empty")


def test_arrays_are_owned_readonly_and_profile_omits_per_population_compute():
    traffic = np.array([[0.0, 3.0], [1.0, 1.0]])
    metadata = {"nested": {"split": "calibration"}}
    result = profile(expected_edge_flits=traffic, metadata=metadata)
    traffic[0, 0] = 100
    metadata["nested"]["split"] = "evaluation"
    assert result.expected_edge_flits[0, 0] == 0
    assert result.metadata["nested"]["split"] == "calibration"
    for name in ARRAY_FIELDS:
        with pytest.raises(ValueError):
            getattr(result, name).flags.writeable = True
    assert "compute_sops" not in {item.name for item in fields(result)}
    assert result.compute_total_sops.ndim == 1


def test_json_roundtrip_and_schema_separation(tmp_path):
    original = build_fingerprint([sample()], "offline:test")
    path = save_fingerprint(tmp_path / "profile.json", original)
    restored = load_fingerprint(path)
    for name in ARRAY_FIELDS:
        np.testing.assert_array_equal(getattr(original, name), getattr(restored, name))
    assert restored.source == original.source and restored.metadata == original.metadata
    assert json.loads(path.read_text())["kind"] == "scheduling_fingerprint"
    with pytest.raises(ValueError):
        load_workload(path)
    with pytest.raises(ValueError, match="kind"):
        load_fingerprint(save_workload(tmp_path / "workload.json", sample()))


@pytest.mark.parametrize("changes", [
    {"pop_size": [True, 8]}, {"pop_size": [0, 8]}, {"edge_src": [0.0, 1]},
    {"edge_src": [0]}, {"edge_dst": [1, 2]},
    {"edge_src": [0, 0], "edge_dst": [1, 1]},
    {"expected_edge_flits": [[0, 1, 2], [0, 0, 0]]},
    {"compute_total_sops": [[1, 2], [3, 4]]}, {"compute_total_sops": [1]},
    {"compute_total_sops": [float("nan"), 1]}, {"compute_total_sops": [-1, 0]},
    {"expected_edge_flits": [[float("inf"), 0], [0, 0]]},
    {"expected_edge_flits": [[-1, 0], [0, 0]]},
    {"compute_total_sops": [1e308, 1e308]},
    {"expected_edge_flits": [[1e308, 0], [0, 0]]},
    {"state_size_mb": True}, {"state_size_mb": float("nan")}, {"source": " "},
    {"metadata": {"bad": float("inf")}},
])
def test_invalid_profiles(changes):
    with pytest.raises(ValueError):
        profile(**changes)


@pytest.mark.parametrize("field,value", [
    ("kind", "workload"), ("schema_version", True), ("schema_version", 2),
    ("units", {"traffic": "packets"}), ("unrecognized", True),
])
def test_json_rejects_ambiguous_or_unknown_fields(tmp_path, field, value):
    path = save_fingerprint(tmp_path / "profile.json", profile())
    document = json.loads(path.read_text())
    document[field] = value
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError):
        load_fingerprint(path)


def test_no_edges_and_no_traffic_are_valid():
    result = profile(edge_src=[], edge_dst=[], expected_edge_flits=[[], []])
    assert result.expected_edge_flits.shape == (2, 0)
    assert result.total_noc_endpoint == result.mean_noc_endpoint == 0
    assert result.total_compute_sops == 12


def test_average_large_finite_compute_avoids_intermediate_sum_overflow():
    calibration = sample(compute_sops=[[1e308, 0], [0, 0]])
    result = build_fingerprint([calibration, calibration], "offline:large")
    assert result.total_compute_sops == 1e308
