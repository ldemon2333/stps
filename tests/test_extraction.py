import numpy as np
import pytest

from fingerprint import EdgeSpec, mask_linear, split_layer
from fingerprint.extractor import workload_from_spike_traces


def test_explicit_topology_preserves_single_sample_compute_and_traffic():
    pops = split_layer("a", "vec", (2,)) + split_layer("b", "vec", (3,))
    edges = [EdgeSpec("a", "b", "linear", lambda s, d: mask_linear(d.size), compute_per_flit=8)]
    traces = {"a": np.array([[[1, 0], [1, 1]], [[0, 0], [1, 0]]]),
              "b": np.zeros((2, 2, 3))}
    w = workload_from_spike_traces(pops, edges, traces, state_size_mb=0,
                                   source="test:known", sample_index=0)
    assert w.edge_expected_flits.tolist() == [[3], [0]]
    assert w.compute_sops.tolist() == [[0, 24], [0, 0]]
    assert w.metadata["sample_aggregation"] == "single"
    mean = workload_from_spike_traces(pops, edges, traces, state_size_mb=0, source="test:known")
    assert mean.edge_expected_flits.tolist() == [[4.5], [1.5]]
    assert mean.compute_sops.tolist() == [[0, 36], [0, 12]]


def test_hook_collection_restores_mode_removes_hooks_and_keeps_uneven_batches():
    torch = pytest.importorskip("torch")
    from fingerprint.dtdg import collect_spike_traces

    class Net(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.spike = torch.nn.Identity()

        def forward(self, x):
            return self.spike(torch.stack([x, x * 0]))
    net = Net()
    resets = []
    result = collect_spike_traces(net, {"spike": net.spike},
        [torch.ones(2, 3), torch.ones(1, 3) * 2], 2, batches=2,
        reset_fn=lambda model: resets.append(model))
    assert result["spike"].shape == (2, 3, 3)
    np.testing.assert_array_equal(result["spike"][0], [[1]*3, [1]*3, [2]*3])
    assert net.training and not net.spike._forward_hooks and len(resets) == 3


def test_hook_error_cleanup():
    torch = pytest.importorskip("torch")
    from fingerprint.dtdg import collect_spike_traces
    net = torch.nn.Identity()
    with pytest.raises(ValueError, match="shape"):
        collect_spike_traces(net, {"a": net}, [torch.ones(1, 3)], 2)
    assert net.training and not net._forward_hooks
