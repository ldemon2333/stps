"""DTDG builder for generic SpikingJelly multi-step nets (docs/fingerprint.md).

Workflow (§Step 1–5):
    1. Hook all spiking neuron modules → collect (T, B, ...) traces.
    2. Auto-discover topology: for each LIF, find its driving op (Linear /
       Conv2d) by walking the module graph in registration order.
    3. Slice each LIF node via split_layer.
    4. Build (T, V', V', 2) edge tensor through edge_builder.
    5. Caller passes the tensor to extract_fingerprint_from_W.

Lazy torch / spikingjelly imports keep the rest of the package torch-free.
"""
from __future__ import annotations

from typing import Iterable, List

import numpy as np

from .edge_builder import EdgeSpec, HaloEdgeSpec, build_edge_tensor
from .mask import mask_conv2d, mask_identity, mask_linear
from .slicing import MicroPopulation, split_layer


class DTDGBuilder:
    """Build a (T, V', V', 2) DTDG weight tensor from a SpikingJelly forward pass."""

    @staticmethod
    def spike_count_timeline_from_spikingjelly(
        net,
        dataloader: Iterable,
        T: int,
        batches: int = 1,
    ) -> np.ndarray:
        """Return E^(t): (T,) val-set sample mean of per-tick spike counts.

        For each sample b in the dataset, E^(t)_b = sum over all LIF nodes and
        all neurons of the spike count at tick t. We average over samples:
            E^(t) = (1/N) Σ_b E^(t)_b
        where N = batches * batch_size. Streaming accumulator avoids the
        per-batch-mean ≠ overall-mean trap when batches are unequal.
        """
        try:
            import torch
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("SpikingJelly path requires torch") from exc

        lif_modules = [(n, m) for n, m in net.named_modules() if _is_spiking(m)]
        if not lif_modules:
            raise ValueError("No spiking neurons found in net")

        # Each hook accumulates the running per-tick spike-count into a shared
        # (T,) tensor for the current batch. We sum across LIF nodes inside the
        # hook itself so we never materialize per-node tensors.
        sum_E = np.zeros(T, dtype=np.float64)
        n_samples = 0
        batch_E = {"value": None}  # (T, B) for the current batch

        def _hook(_mod, _inp, out):
            arr = out.detach()
            # SpikingJelly multi-step: leading dim is T or T*B.
            if arr.shape[0] == T:
                # (T, B, ...) -> sum over all non-{T,B} dims -> (T, B)
                reduced = arr.flatten(2).sum(dim=2) if arr.dim() > 2 else arr
            elif arr.shape[0] % T == 0:
                B = arr.shape[0] // T
                reduced = arr.reshape(T, B, -1).sum(dim=2)
            else:
                raise ValueError(
                    f"LIF trace leading dim {arr.shape[0]} not compatible with T={T}"
                )
            reduced_np = reduced.float().cpu().numpy()
            if batch_E["value"] is None:
                batch_E["value"] = reduced_np.astype(np.float64)
            else:
                batch_E["value"] = batch_E["value"] + reduced_np.astype(np.float64)

        hooks = [m.register_forward_hook(_hook) for _, m in lif_modules]

        try:
            net.eval()
            with torch.no_grad():
                for b, batch in enumerate(dataloader):
                    if b >= batches:
                        break
                    batch_E["value"] = None
                    x = batch[0] if isinstance(batch, (list, tuple)) else batch
                    net(x)
                    if batch_E["value"] is None:
                        continue
                    arr = batch_E["value"]  # (T, B)
                    sum_E += arr.sum(axis=1)
                    n_samples += arr.shape[1]
        finally:
            for h in hooks:
                h.remove()

        if n_samples == 0:
            return np.zeros(T, dtype=np.float32)
        return (sum_E / n_samples).astype(np.float32)

    @staticmethod
    def from_spikingjelly(
        net,
        dataloader: Iterable,
        T: int,
        batches: int = 1,
        N_core_cap: int = 4096,
    ) -> np.ndarray:
        try:
            import torch
            from torch import nn
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("SpikingJelly path requires torch") from exc

        # Find every spiking neuron + every Linear/Conv2d in registration order.
        modules_in_order: List[tuple] = []  # [(name, module)]
        lif_set = set()
        for name, mod in net.named_modules():
            if _is_spiking(mod):
                modules_in_order.append((name, mod))
                lif_set.add(id(mod))
            elif isinstance(mod, (nn.Linear, nn.Conv2d)):
                modules_in_order.append((name, mod))

        lif_modules = [(n, m) for n, m in modules_in_order if id(m) in lif_set]
        if not lif_modules:
            raise ValueError("No spiking neurons found in net")

        # Hook every LIF (collects (T*B, ...) — SpikingJelly multi-step convention).
        traces: dict = {n: [] for n, _ in lif_modules}
        hooks = []
        for name, m in lif_modules:
            def _hook(_mod, _inp, out, name=name):
                arr = out.detach().float().cpu()
                traces[name].append(arr)
            hooks.append(m.register_forward_hook(_hook))

        try:
            net.eval()
            with torch.no_grad():
                for b, batch in enumerate(dataloader):
                    if b >= batches:
                        break
                    x = batch[0] if isinstance(batch, (list, tuple)) else batch
                    net(x)
        finally:
            for h in hooks:
                h.remove()

        # Reduce traces from list-of-(TB,...) to (T, B_total, U) per LIF.
        spike_traces: dict = {}
        node_shapes: dict = {}  # name -> ("vec"/"fmap"/"token_embed", shape_meta)
        for name, _ in lif_modules:
            tlist = traces.get(name, [])
            if not tlist:
                spike_traces[name] = np.zeros((T, 1, 1), dtype=np.float32)
                node_shapes[name] = ("vec", (1,))
                continue
            chunks = []
            for arr in tlist:
                a = arr.numpy().astype(np.float32)
                # Try (T,B,...) first; fall back to (TB, ...) split by T.
                if a.shape[0] == T:
                    chunks.append(a)
                elif a.shape[0] % T == 0:
                    B = a.shape[0] // T
                    chunks.append(a.reshape(T, B, *a.shape[1:]))
                else:
                    raise ValueError(
                        f"LIF {name} trace leading dim {a.shape[0]} not "
                        f"compatible with T={T}"
                    )
            stacked = np.concatenate(chunks, axis=1)  # along batch
            sample_shape = stacked.shape[2:]  # tail dims after (T,B)
            kind, shape_meta = _classify_shape(sample_shape)
            U = int(np.prod(sample_shape)) if sample_shape else 1
            spike_traces[name] = stacked.reshape(T, stacked.shape[1], U)
            node_shapes[name] = (kind, shape_meta)

        # Slice every LIF node.
        pops: List[MicroPopulation] = []
        node_to_global_idx: dict = {}
        for name, _ in lif_modules:
            kind, shape_meta = node_shapes[name]
            shards = split_layer(name, kind, shape_meta, N_core_cap=N_core_cap)
            node_to_global_idx[name] = list(
                range(len(pops), len(pops) + len(shards))
            )
            pops.extend(shards)

        # Auto-discover edges: each LIF's nearest preceding (Linear/Conv2d) op
        # in registration order defines the *incoming* edge from the previous
        # LIF. We pair (prev_LIF, this_LIF, op_kind).
        edges: List[EdgeSpec] = []
        prev_lif_name = None
        pending_op = None  # last seen Linear/Conv2d
        lif_names = {n for n, _ in lif_modules}
        for name, mod in modules_in_order:
            if name in lif_names:
                if prev_lif_name is not None and pending_op is not None:
                    edges.append(_build_edge(prev_lif_name, name, pending_op))
                    pending_op = None
                prev_lif_name = name
            else:
                pending_op = mod  # remember the most recent linear op
        # Halo edges from slicing
        halo: List[HaloEdgeSpec] = []
        for shard in pops:
            for sib_local, flits in shard.halo_neighbors:
                # halo_neighbors stores absolute shard_id within the same node;
                # convert to global index.
                sib_global = node_to_global_idx[shard.node_id][sib_local]
                src_global = node_to_global_idx[shard.node_id][shard.shard_id]
                halo.append(HaloEdgeSpec(
                    src_global_idx=src_global,
                    dst_global_idx=sib_global,
                    flits_per_step=int(flits),
                ))

        return build_edge_tensor(pops, edges, spike_traces, T=T, halo_edges=halo)


def _is_spiking(mod) -> bool:
    name = type(mod).__name__
    if name.endswith(("Node", "Neuron")):
        return True
    # SpikingJelly: BaseNode subclass
    try:
        from spikingjelly.activation_based.neuron import BaseNode  # type: ignore
        if isinstance(mod, BaseNode):
            return True
    except Exception:
        pass
    # STEP / braincog
    try:
        from braincog.base.node.node import BaseNode as BcBaseNode  # type: ignore
        if isinstance(mod, BcBaseNode):
            return True
    except Exception:
        pass
    return False


def _classify_shape(sample_shape: tuple) -> tuple:
    """Map a per-sample LIF output shape to (kind, shape_meta)."""
    if len(sample_shape) == 0:
        return ("vec", (1,))
    if len(sample_shape) == 1:
        return ("vec", (int(sample_shape[0]),))
    if len(sample_shape) == 2:
        return ("token_embed", (int(sample_shape[0]), int(sample_shape[1])))
    if len(sample_shape) == 3:
        C, H, W = sample_shape
        return ("fmap", (int(C), int(H), int(W)))
    # Higher-rank → flatten to vec
    return ("vec", (int(np.prod(sample_shape)),))


def _build_edge(src: str, dst: str, op) -> EdgeSpec:
    try:
        from torch import nn
    except ImportError:  # pragma: no cover
        raise

    if isinstance(op, nn.Conv2d):
        K = int(op.kernel_size[0]) if isinstance(op.kernel_size, tuple) else int(op.kernel_size)
        return EdgeSpec(
            src=src, dst=dst, kind="conv2d",
            mask_factory=lambda src_pop, dst_pop, K=K: mask_conv2d(
                K=K,
                C_out_shard=_dst_channel_count(dst_pop),
            ),
        )
    if isinstance(op, nn.Linear):
        return EdgeSpec(
            src=src, dst=dst, kind="linear",
            mask_factory=lambda src_pop, dst_pop: mask_linear(dst_pop.size),
        )
    return EdgeSpec(
        src=src, dst=dst, kind="identity",
        mask_factory=lambda src_pop, dst_pop: mask_identity(),
    )


def _dst_channel_count(dst_pop: MicroPopulation) -> int:
    """C_out_shard for an fmap shard, or fall back to dst size."""
    if dst_pop.kind == "fmap":
        c_lo, c_hi = dst_pop.meta.get("c_range", (0, 1))
        return max(1, int(c_hi - c_lo))
    return max(1, dst_pop.size)
