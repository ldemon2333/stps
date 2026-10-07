"""Explicit graph workloads; aggregate E-only fingerprints are not supported."""
from .workload import Workload, from_edge_tensor, load_workload, make_sparse_workload, save_workload
from .slicing import MicroPopulation, split_layer
from .edge_builder import EdgeSpec, HaloEdgeSpec, build_edge_tensor
from .mask import SparsityMask, mask_conv2d, mask_identity, mask_linear, mask_pruned

__all__ = [
    "Workload", "from_edge_tensor", "load_workload", "save_workload", "make_sparse_workload",
    "MicroPopulation", "split_layer", "EdgeSpec", "HaloEdgeSpec", "build_edge_tensor",
    "SparsityMask", "mask_conv2d", "mask_identity", "mask_linear", "mask_pruned",
]
