"""Same-source traffic/SOP workloads from explicit topology and node spikes."""
import numpy as np

from .edge_builder import build_edge_tensor
from .workload import from_edge_tensor


def workload_from_spike_traces(pops, edges, spike_traces, *, state_size_mb: float,
                                source: str, sample_index: int | None = None,
                                halo_edges=None, metadata=None):
    """Select one sample for BOTH traffic and compute, or average all samples.

    compute_per_flit is an operator assumption, not measured core service time.
    Caller supplies graph topology and flatten order matching MicroPopulation.
    """
    if not pops or not spike_traces:
        raise ValueError("MicroPopulations and spike traces are required")
    sizes = {}
    for pop in pops:
        sizes[pop.node_id] = sizes.get(pop.node_id, 0) + pop.size
    if set(sizes) != set(spike_traces):
        raise ValueError("trace nodes must exactly match MicroPopulation nodes")
    selected, shape = {}, None
    for node, data in spike_traces.items():
        a = np.asarray(data)
        if (a.ndim != 3 or a.shape[0] < 1 or a.shape[1] < 1 or a.shape[2] != sizes[node]
                or a.dtype.kind not in "iufb" or not np.isfinite(a).all() or (a < 0).any()):
            raise ValueError(f"{node}: expected finite nonnegative T x B x units matching slices")
        if shape is not None and a.shape[:2] != shape:
            raise ValueError("all traces must share T and sample count")
        shape = a.shape[:2]
        if sample_index is not None:
            if type(sample_index) is not int or not 0 <= sample_index < a.shape[1]:
                raise ValueError("sample_index out of range")
            a = a[:, sample_index:sample_index + 1]
        selected[node] = a
    for edge in edges:
        if edge.src not in sizes or edge.dst not in sizes:
            raise ValueError("edge references unknown node")
        if not np.isfinite(edge.compute_per_flit) or edge.compute_per_flit < 0:
            raise ValueError("compute_per_flit must be finite and nonnegative")
    W = build_edge_tensor(pops, edges, selected, T=shape[0], halo_edges=halo_edges)
    details = dict(metadata or {})
    details.update(sample_index=sample_index, samples=shape[1],
                   compute_model="target SOP proxy from explicit operator compute_per_flit",
                   sample_aggregation="single" if sample_index is not None else "mean",
                   nodes=[{"node_id": p.node_id, "shard_id": p.shard_id} for p in pops])
    return from_edge_tensor(W, [p.size for p in pops], state_size_mb, source, details)
