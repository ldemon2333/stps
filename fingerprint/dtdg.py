"""Capture named spike modules without inferring topology from registration order."""
from __future__ import annotations
import numpy as np


def collect_spike_traces(net, nodes: dict, dataloader, T: int, *, batches: int = 1,
                         reset_fn=None) -> dict[str, np.ndarray]:
    """Collect T x samples x units from T x B x ... or (T*B) x ... outputs.

    The caller supplies actual spike-producing modules and owns device setup
    and SNN state reset. reset_fn(net) runs before each batch and at exit.
    Multiple calls of one module per forward require an explicit time adapter.
    """
    import torch
    if type(T) is not int or T < 1 or type(batches) is not int or batches < 1:
        raise ValueError("T and batches must be positive integers")
    if not nodes or any(not isinstance(name, str) or not name for name in nodes):
        raise ValueError("nodes must name spike-producing modules")
    if len({id(module) for module in nodes.values()}) != len(nodes):
        raise ValueError("each module must have one node ID")
    collected = {name: [] for name in nodes}
    current, handles = {}, []
    modes = {module: module.training for module in net.modules()}
    batch_size = 0

    def hook(name):
        def receive(_module, _inputs, output):
            if name in current:
                raise ValueError(f"{name} ran more than once; explicit time adapter needed")
            if not isinstance(output, torch.Tensor):
                raise ValueError(f"{name}: spike output must be a tensor")
            a = output.detach().float().cpu().numpy()
            if a.ndim >= 2 and a.shape[:2] == (T, batch_size):
                a = a.reshape(T, batch_size, -1)
            elif a.ndim >= 1 and a.shape[0] == T * batch_size:
                a = a.reshape(T, batch_size, -1)
            else:
                raise ValueError(f"{name}: shape {a.shape} incompatible with T={T}, B={batch_size}")
            if not np.isfinite(a).all() or (a < 0).any():
                raise ValueError(f"{name}: spikes must be finite and nonnegative")
            current[name] = a.copy()
        return receive

    try:
        for name, module in nodes.items():
            handles.append(module.register_forward_hook(hook(name)))
        net.eval()
        with torch.no_grad():
            for index, batch in enumerate(dataloader):
                if index >= batches:
                    break
                if reset_fn is not None:
                    reset_fn(net)
                current.clear()
                x = batch[0] if isinstance(batch, (list, tuple)) else batch
                batch_size = int(x.shape[0])
                if batch_size < 1:
                    raise ValueError("empty model batch")
                net(x)
                if set(current) != set(nodes):
                    raise ValueError(f"nodes did not execute: {sorted(set(nodes) - set(current))}")
                for name, arr in current.items():
                    collected[name].append(arr)
        if not any(collected.values()):
            raise ValueError("no calibration samples collected")
        return {name: np.concatenate(chunks, axis=1) for name, chunks in collected.items()}
    finally:
        for handle in handles:
            handle.remove()
        try:
            if reset_fn is not None:
                reset_fn(net)
        finally:
            for module, training in modes.items():
                module.training = training
