from __future__ import annotations

import subprocess
import sys

import numpy as np
import pytest

from fingerprint import (
    Fingerprint,
    extract_fingerprint_from_W,
    load_fingerprint,
    make_synthetic_fingerprint,
    power_iteration_in_eigen_centrality,
    save_fingerprint,
)


def test_fingerprint_round_trip_preserves_schema(tmp_path):
    fp = Fingerprint(
        mean_injection_trace=np.arange(8, dtype=np.float32),
        global_burstiness=2.5,
        max_centrality=np.array([0.2, 0.8], dtype=np.float32),
        mean_components=1.0,
        T=8,
        neuron_count=128,
        state_size_mb=4.0,
        complexity_ratio=1.0,
        compute_sequence=np.zeros(8, dtype=np.float32),
        centrality_var=np.full(8, 0.1, dtype=np.float32),
        meta={"model": "synthetic", "dataset": "unit"},
    )

    path = tmp_path / "fp.npz"
    save_fingerprint(path, fp)
    loaded = load_fingerprint(path)

    np.testing.assert_array_equal(loaded.mean_injection_trace, fp.mean_injection_trace)
    np.testing.assert_array_equal(loaded.traffic_sequence, fp.mean_injection_trace)
    np.testing.assert_array_equal(loaded.centrality_var, fp.centrality_var)
    np.testing.assert_array_equal(loaded.max_centrality, fp.max_centrality)
    assert loaded.global_burstiness == pytest.approx(fp.global_burstiness)
    assert loaded.mean_components == pytest.approx(fp.mean_components)
    assert loaded.T == fp.T
    assert loaded.meta == fp.meta


def test_in_eigenvector_centrality_concentrates_on_receiver_hub():
    W = np.zeros((5, 5), dtype=np.float32)
    W[1:, 0] = 1.0

    c = power_iteration_in_eigen_centrality(W, iters=200, tol=1e-9)

    assert int(np.argmax(c)) == 0
    assert c[0] > 0.7
    assert np.isclose(float(c.sum()), 1.0)


def test_centrality_returns_finite_uniform_vector_for_empty_graph():
    c = power_iteration_in_eigen_centrality(np.zeros((4, 4), dtype=np.float32))

    assert np.all(np.isfinite(c))
    np.testing.assert_allclose(c, np.full(4, 0.25, dtype=np.float32))


def test_extractor_identifies_burst_and_connected_components():
    W = np.zeros((6, 4, 4), dtype=np.float32)
    W[:, 0, 1] = 1.0
    W[:, 2, 3] = 1.0
    W[3] += 25.0

    fp = extract_fingerprint_from_W(
        W, neuron_count=64, state_size_mb=8.0, meta={"case": "bursty"}
    )

    assert fp.T == 6
    assert fp.traffic_sequence.shape == (6,)
    assert int(np.argmax(fp.traffic_sequence)) == 3
    assert fp.global_burstiness > 3.0
    assert fp.mean_components >= 1.0
    assert fp.meta["case"] == "bursty"


def test_extractor_rejects_non_square_or_non_temporal_tensor():
    with pytest.raises(ValueError, match="must be"):
        extract_fingerprint_from_W(
            np.zeros((3, 2, 4), dtype=np.float32), neuron_count=4, state_size_mb=1.0
        )

    with pytest.raises(ValueError, match="must be"):
        extract_fingerprint_from_W(
            np.zeros((3, 3), dtype=np.float32), neuron_count=3, state_size_mb=1.0
        )


def test_synthetic_fingerprint_is_valid_and_deterministic():
    fp1 = make_synthetic_fingerprint(beta_target=4.0, K=2, T=16, V=8, seed=7)
    fp2 = make_synthetic_fingerprint(beta_target=4.0, K=2, T=16, V=8, seed=7)

    assert fp1.T == 16
    assert fp1.traffic_sequence.shape == (16,)
    assert fp1.max_centrality.shape == (8,)
    assert fp1.global_burstiness > 1.0
    assert fp1.mean_components == pytest.approx(2.0)
    np.testing.assert_array_equal(fp1.traffic_sequence, fp2.traffic_sequence)
    np.testing.assert_array_equal(fp1.max_centrality, fp2.max_centrality)


def test_fingerprint_cli_creates_loadable_npz(tmp_path):
    out = tmp_path / "cli_fp.npz"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "fingerprint.cli",
            "--synthetic",
            "--T",
            "12",
            "--beta",
            "3.0",
            "--K",
            "2",
            "--seed",
            "5",
            "--out",
            str(out),
        ],
        text=True,
        capture_output=True,
        check=True,
    )

    assert out.exists()
    assert "Fingerprint(T=12" in result.stdout
    loaded = load_fingerprint(out)
    assert loaded.T == 12
    assert loaded.meta["source"] == "synthetic"


def test_fingerprint_round_trip_preserves_sample_measured_trace_schema(tmp_path):
    fp = Fingerprint(
        mean_injection_trace=np.array([2.0, 4.0, 6.0], dtype=np.float32),
        global_burstiness=1.5,
        max_centrality=np.array([0.25, 0.75], dtype=np.float32),
        mean_components=1.0,
        T=3,
        neuron_count=2,
        state_size_mb=1.0,
        complexity_ratio=1.0,
        compute_sequence=np.zeros(3, dtype=np.float32),
        centrality_var=np.zeros(3, dtype=np.float32),
        sample_measured_injection_trace=np.array([1.0, 3.0, 5.0], dtype=np.float32),
        sample_index=7,
        sample_label=3,
        sample_path="cifar10/test/7",
        meta={"model": "unit"},
    )

    path = tmp_path / "sample_fp.npz"
    save_fingerprint(path, fp)
    loaded = load_fingerprint(path)

    np.testing.assert_array_equal(
        loaded.sample_measured_injection_trace,
        fp.sample_measured_injection_trace,
    )
    assert loaded.sample_index == fp.sample_index
    assert loaded.sample_label == fp.sample_label
    assert loaded.sample_path == fp.sample_path
    np.testing.assert_array_equal(loaded.mean_injection_trace, fp.mean_injection_trace)


def test_spike_timeline_returns_sample_metadata_for_each_trace():
    torch = pytest.importorskip("torch")
    import fingerprint.extract_spikformer as extractor
    from fingerprint.extract_spikformer import SampleBatch, spike_count_timeline

    class ToyNode(torch.nn.Module):
        def forward(self, x):
            T = 3
            B = x.shape[0]
            base = x.reshape(1, B, 1, 1, 1).repeat(T, 1, 1, 1, 1)
            return base + torch.arange(T, dtype=x.dtype).reshape(T, 1, 1, 1, 1)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(extractor, "_is_spiking_neuron", lambda module: isinstance(module, ToyNode))
    net = ToyNode()
    batch = SampleBatch(
        images=torch.tensor([1.0, 10.0]).reshape(2, 1, 1, 1),
        labels=torch.tensor([4, 7], dtype=torch.int64),
        indices=torch.tensor([11, 12], dtype=torch.int64),
        paths=["cifar10/test/11", "cifar10/test/12"],
    )

    E, neuron_count, traces, indices, labels, paths = spike_count_timeline(
        net, [batch], T=3, batches=1
    )

    assert neuron_count == 1
    np.testing.assert_array_equal(indices, np.array([11, 12], dtype=np.int32))
    np.testing.assert_array_equal(labels, np.array([4, 7], dtype=np.int32))
    np.testing.assert_array_equal(paths, np.array(["cifar10/test/11", "cifar10/test/12"]))
    np.testing.assert_allclose(traces, np.array([[1, 2, 3], [10, 11, 12]], dtype=np.float32))
    np.testing.assert_allclose(E, np.array([5.5, 6.5, 7.5], dtype=np.float32))
    monkeypatch.undo()


def test_spike_timeline_handles_batch_size_one_flattened_time_batch_output():
    torch = pytest.importorskip("torch")
    import fingerprint.extract_spikformer as extractor
    from fingerprint.extract_spikformer import SampleBatch, spike_count_timeline

    class FlatToyNode(torch.nn.Module):
        def forward(self, x):
            T = 4
            return torch.arange(T, dtype=x.dtype).reshape(T, 1, 1, 1) + x.reshape(1, 1, 1, 1)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(extractor, "_is_spiking_neuron", lambda module: isinstance(module, FlatToyNode))
    batch = SampleBatch(
        images=torch.tensor([5.0]).reshape(1, 1, 1, 1),
        labels=torch.tensor([3], dtype=torch.int64),
        indices=torch.tensor([0], dtype=torch.int64),
        paths=["cifar10/test/0"],
    )

    E, neuron_count, traces, indices, labels, paths = spike_count_timeline(
        FlatToyNode(), [batch], T=4, batches=1
    )

    assert neuron_count == 1
    np.testing.assert_allclose(traces, np.array([[5, 6, 7, 8]], dtype=np.float32))
    np.testing.assert_allclose(E, np.array([5, 6, 7, 8], dtype=np.float32))
    np.testing.assert_array_equal(indices, np.array([0], dtype=np.int32))
    np.testing.assert_array_equal(labels, np.array([3], dtype=np.int32))
    np.testing.assert_array_equal(paths, np.array(["cifar10/test/0"]))
    monkeypatch.undo()


def test_fingerprint_round_trip_writes_mean_and_single_sample_trace_fields(tmp_path):
    fp = Fingerprint(
        mean_injection_trace=np.array([2.0, 4.0, 6.0], dtype=np.float32),
        global_burstiness=1.5,
        max_centrality=np.array([0.25, 0.75], dtype=np.float32),
        mean_components=1.0,
        T=3,
        neuron_count=2,
        state_size_mb=1.0,
        complexity_ratio=1.0,
        compute_sequence=np.zeros(3, dtype=np.float32),
        centrality_var=np.zeros(3, dtype=np.float32),
        sample_measured_injection_trace=np.array([1.0, 3.0, 5.0], dtype=np.float32),
        sample_index=7,
        sample_label=3,
        sample_path="cifar10/test/7",
        meta={"model": "unit"},
    )

    path = tmp_path / "renamed_fp.npz"
    save_fingerprint(path, fp)
    z = np.load(path, allow_pickle=False)

    assert "mean_injection_trace" in z.files
    assert "sample_measured_injection_trace" in z.files
    assert "traffic_sequence" not in z.files
    assert "sample_measured_injection_traces" not in z.files

    loaded = load_fingerprint(path)
    np.testing.assert_array_equal(loaded.mean_injection_trace, fp.mean_injection_trace)
    np.testing.assert_array_equal(loaded.traffic_sequence, fp.mean_injection_trace)
    np.testing.assert_array_equal(
        loaded.sample_measured_injection_trace,
        fp.sample_measured_injection_trace,
    )
    assert loaded.sample_index == 7
    assert loaded.sample_label == 3
    assert loaded.sample_path == "cifar10/test/7"
