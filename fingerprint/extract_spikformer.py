"""Adapter: turn a STEP/Spikformer instance into a v3 fingerprint .npz.

Implements docs/fingerprint.md §6 (5-step AOT workflow) for Spikformer:
    Step 1  Calibration sampling (CIFAR test set or dummy)
    Step 2  Fine-grained spike hooking (BaseNode_Torch)
    Step 3  Hardware-aware translation (split_layer + Sparsity Mask)
    Step 4  Mean-over-B expectation (E_b[·] inside edge_builder)
    Step 5  Graph reduction (extract_fingerprint_from_W)

CLI:
    python -m fingerprint.extract_spikformer \
        --dataset cifar10 --T 4 \
        --checkpoint model/STEP/spikformer_cifar_pth.tar \
        --data-dir dataset/cifar-10-batches-py/test_batch \
        --batch-size 8 --num-batches 4 \
        --out npz/spikformer_cifar10.npz
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Iterable, List

import numpy as np

from . import (
    Fingerprint,
    extract_fingerprint_from_W,
    save_fingerprint,
)
from .edge_builder import EdgeSpec, HaloEdgeSpec, build_edge_tensor
from .mask import mask_conv2d, mask_identity, mask_linear
from .slicing import MicroPopulation, split_layer

REPO_ROOT = Path(__file__).resolve().parent.parent
STEP_CLS_DIR = REPO_ROOT / "model" / "STEP" / "cls"

DATASET_CFG = {
    "cifar10": (32, 10, 3,
                (0.4914, 0.4822, 0.4465), (0.2470, 0.2435, 0.2616)),
    "cifar100": (32, 100, 3,
                 (0.5071, 0.4865, 0.4409), (0.2673, 0.2564, 0.2762)),
}


def _ensure_step_on_path() -> None:
    if str(STEP_CLS_DIR) not in sys.path:
        sys.path.insert(0, str(STEP_CLS_DIR))


def build_network(dataset: str, T: int, img_size: int, depths: int = 4,
                  embed_dim: int = 384, num_heads: int = 12):
    _ensure_step_on_path()
    from models.static.spikformer_cifar import Spikformer  # type: ignore
    from models.utils.node import LIFNode  # type: ignore
    from models.utils.surrogate import SigmoidGrad  # type: ignore

    _, num_classes, in_channels, _, _ = DATASET_CFG[dataset]
    return Spikformer(
        step=T, img_size=img_size, patch_size=4,
        in_channels=in_channels, num_classes=num_classes,
        embed_dim=embed_dim, num_heads=num_heads,
        mlp_ratio=4, attn_scale=0.125, attn_drop=0.0,
        depths=depths, node=LIFNode, tau=2.0, threshold=1.0,
        act_func=SigmoidGrad, alpha=4.0,
        embed_layer="SPS", attn_layer="SSA", layer_by_layer=True,
    )


def load_checkpoint(net, ckpt_path: str) -> None:
    import torch

    state = torch.load(ckpt_path, map_location="cpu")
    if isinstance(state, dict):
        for k in ("state_dict", "model", "model_state_dict"):
            if k in state and isinstance(state[k], dict):
                state = state[k]
                break
    state = {k.removeprefix("module."): v for k, v in state.items()}
    missing, unexpected = net.load_state_dict(state, strict=False)
    if missing:
        print(f"  load_checkpoint: {len(missing)} missing keys (first 3): {missing[:3]}")
    if unexpected:
        print(f"  load_checkpoint: {len(unexpected)} unexpected keys (first 3): {unexpected[:3]}")


def make_dummy_loader(batch_size: int, img_size: int, in_channels: int):
    import torch
    return [(torch.randn(batch_size, in_channels, img_size, img_size),)]


def make_cifar_loader(data_dir: Path, dataset: str, batch_size: int,
                      img_size: int, num_batches: int):
    """Load CIFAR test set from raw pickle or torchvision layout."""
    import pickle
    import torch
    from torch.utils.data import DataLoader, TensorDataset
    from torchvision import datasets, transforms

    _, _, _, mean, std = DATASET_CFG[dataset]
    tfm = transforms.Compose([
        transforms.Resize(img_size),
        transforms.CenterCrop(img_size),
        transforms.ToTensor(),
        transforms.Normalize(mean, std),
    ])

    if data_dir.is_file():
        with open(data_dir, "rb") as f:
            d = pickle.load(f, encoding="bytes")
        raw = d[b"data"].reshape(-1, 3, 32, 32).astype(np.float32) / 255.0
        labels = np.array(d.get(b"labels", d.get(b"fine_labels", [])), dtype=np.int64)
        m = np.array(mean, dtype=np.float32).reshape(1, 3, 1, 1)
        s = np.array(std, dtype=np.float32).reshape(1, 3, 1, 1)
        x = torch.from_numpy((raw - m) / s)
        if img_size != 32:
            x = torch.nn.functional.interpolate(
                x, size=img_size, mode="bilinear", align_corners=False)
        ds = TensorDataset(x, torch.from_numpy(labels))
    else:
        cls = datasets.CIFAR100 if dataset == "cifar100" else datasets.CIFAR10
        ds = cls(str(data_dir), train=False, download=True, transform=tfm)

    loader = DataLoader(ds, batch_size=batch_size, shuffle=False, num_workers=0)
    out = []
    for i, batch in enumerate(loader):
        if i >= num_batches:
            break
        out.append(batch)
    return out


def _is_spiking_neuron(module) -> bool:
    _ensure_step_on_path()
    try:
        from models.utils.node import BaseNode_Torch  # type: ignore
        return isinstance(module, BaseNode_Torch)
    except Exception:
        return type(module).__name__.endswith(("Node",))


def collect_topology(net, T: int) -> tuple:
    """Walk the Spikformer module tree and emit (lif_modules_in_order, edges_meta).

    edges_meta is a list of (src_lif_name, dst_lif_name, op_kind, op_meta)
    capturing the linear op driving each forward edge.

    Spikformer architecture (per spikformer_cifar.py):
        SPS:       proj_conv → BN → proj_lif
                   proj_conv1 → BN → proj_lif1
                   proj_conv2 → BN → proj_lif2 → maxpool
                   proj_conv3 → BN → proj_lif3 → maxpool
                   rpe_conv → BN → rpe_lif (with residual add to feat)
        Block.SSA: q/k/v_linear → BN → q/k/v_lif
                   attn_lif (after attn @ v)
                   proj_linear → BN → proj_lif
        Block.MLP: fc1_linear → BN → fc1_lif
                   fc2_linear → BN → fc2_lif
        Residual edges: x = x + attn(x); x = x + mlp(x)

    We model each LIF as a node, with one edge from its driving op's prior
    LIF (or input). Residual adds become identity edges from the upstream
    LIF into the LIF that follows the add.
    """
    import torch.nn as nn

    lif_modules: List[tuple] = []  # [(name, mod, kind, shape_meta)]
    name_to_op: dict = {}  # lif_name -> (op_kind, op_meta) of driving linear op

    # Walk modules in registration order; for each LIF remember the most
    # recent Linear/Conv2d as its driving op.
    modules_seq: List[tuple] = list(net.named_modules())

    # First pass: find LIFs and their driving op (the nearest preceding
    # Linear/Conv2d in registration order).
    last_op = None  # (kind, meta)
    last_lif_name = None
    edges: List[tuple] = []  # (src_lif_name or None, dst_lif_name, kind, meta)
    for name, mod in modules_seq:
        if isinstance(mod, nn.Conv2d):
            K = int(mod.kernel_size[0]) if isinstance(mod.kernel_size, tuple) else int(mod.kernel_size)
            last_op = ("conv2d", {"K": K, "C_out": int(mod.out_channels)})
        elif isinstance(mod, nn.Linear):
            last_op = ("linear", {"out_features": int(mod.out_features)})
        elif _is_spiking_neuron(mod):
            lif_modules.append((name, mod, None, None))  # shape filled later
            if last_op is not None:
                edges.append((last_lif_name, name, last_op[0], last_op[1]))
                last_op = None
            last_lif_name = name

    # Spikformer residual edges (modeled as identity, M_ij=1 per user spec).
    # Block.forward: x = x + attn(x); x = x + mlp(x)
    # In LIF-graph terms:
    #   - within attn: input to SSA is the LIF coming out of patch_embed (or
    #     the previous block's mlp.fc2_lif). The output of SSA is proj_lif.
    #     Residual add merges those two streams into the input of MLP.fc1.
    # We add an identity edge from the SSA-input LIF to the SSA-output LIF
    # (proj_lif), and from the MLP-input LIF to the MLP-output LIF (fc2_lif).
    # Easier: identify Block.attn.proj_lif → Block.mlp.fc2_lif as residual
    # carriers. Practical detection: any LIF whose name ends ".proj_lif" or
    # ".fc2_lif" gets an extra identity edge from the LIF feeding the block.
    # We approximate by adding identity edges between adjacent block boundaries.
    block_attn_outputs = [n for n, _, _, _ in lif_modules if n.endswith(".attn.proj_lif")]
    block_mlp_outputs = [n for n, _, _, _ in lif_modules if n.endswith(".mlp.fc2_lif")]

    # Bind: residual adds carry the *block input* into the block output. The
    # block input for block[k].attn is either patch_embed output or
    # block[k-1].mlp.fc2_lif. The block input for block[k].mlp is
    # block[k].attn.proj_lif (after residual). We add identity edges:
    #   in_lif → attn.proj_lif      (residual around attn)
    #   attn.proj_lif → mlp.fc2_lif (residual around mlp)
    patch_embed_lif = next(
        (n for n, _, _, _ in lif_modules if n.endswith("patch_embed.rpe_lif")),
        None,
    )
    prev_block_out = patch_embed_lif
    for ao, mo in zip(block_attn_outputs, block_mlp_outputs):
        if prev_block_out is not None:
            edges.append((prev_block_out, ao, "identity", {}))
        edges.append((ao, mo, "identity", {}))
        prev_block_out = mo

    return lif_modules, edges


def shape_from_trace(trace_chunk: "np.ndarray", T: int) -> tuple:
    """Given (TB, ...) trace, infer (kind, shape_meta) for split_layer."""
    if trace_chunk.shape[0] % T != 0:
        raise ValueError(f"Trace TB dim {trace_chunk.shape[0]} not divisible by T={T}")
    sample_shape = trace_chunk.shape[1:]
    if len(sample_shape) == 0:
        return ("vec", (1,))
    if len(sample_shape) == 1:
        return ("vec", (int(sample_shape[0]),))
    if len(sample_shape) == 2:
        return ("token_embed", (int(sample_shape[0]), int(sample_shape[1])))
    if len(sample_shape) == 3:
        C, H, W = sample_shape
        return ("fmap", (int(C), int(H), int(W)))
    return ("vec", (int(np.prod(sample_shape)),))


def build_W_for_spikformer(net, dataloader: Iterable, T: int, batches: int,
                           N_core_cap: int) -> tuple:
    """Run forward, slice every LIF, build (T,V',V',2) edge tensor.

    Returns (W, V_prime, V_orig, total_pop_size).
    """
    import torch

    lif_meta, edges = collect_topology(net, T)
    lif_names = [n for n, _, _, _ in lif_meta]
    lif_set = set(lif_names)

    raw_traces: dict = {n: [] for n in lif_names}
    hooks = []
    name_to_module = dict((n, m) for n, m, _, _ in lif_meta)
    for n in lif_names:
        m = name_to_module[n]
        def _hook(_mod, _inp, out, n=n):
            arr = out.detach().float().cpu().numpy().astype(np.float32)
            raw_traces[n].append(arr)
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

    # Reshape (TB, ...) → (T, B, U); concatenate batches along axis 1.
    spike_traces: dict = {}
    node_shapes: dict = {}
    for n in lif_names:
        chunks = raw_traces[n]
        if not chunks:
            spike_traces[n] = np.zeros((T, 1, 1), dtype=np.float32)
            node_shapes[n] = ("vec", (1,))
            continue
        per_chunk = []
        kind = shape_meta = None
        for arr in chunks:
            if arr.shape[0] % T != 0:
                raise ValueError(f"LIF {n} TB dim {arr.shape[0]} not divisible by T={T}")
            B = arr.shape[0] // T
            sample_shape = arr.shape[1:]
            U = int(np.prod(sample_shape)) if sample_shape else 1
            per_chunk.append(arr.reshape(T, B, U))
            kind, shape_meta = shape_from_trace(arr, T)
        stacked = np.concatenate(per_chunk, axis=1)
        spike_traces[n] = stacked
        node_shapes[n] = (kind, shape_meta)

    # Slice every LIF.
    pops: List[MicroPopulation] = []
    node_to_global_idx: dict = {}
    for n in lif_names:
        kind, shape_meta = node_shapes[n]
        # Choose K for halo flits if fmap; default 3.
        K_hint = 3
        shards = split_layer(n, kind, shape_meta, N_core_cap=N_core_cap, K=K_hint)
        node_to_global_idx[n] = list(range(len(pops), len(pops) + len(shards)))
        pops.extend(shards)

    # Build EdgeSpecs.
    edge_specs: List[EdgeSpec] = []
    for src, dst, kind, meta in edges:
        if src is None or src not in lif_set or dst not in lif_set:
            continue
        edge_specs.append(_make_edge_spec(src, dst, kind, meta))

    # Halo edges (sibling shards within same node).
    halo: List[HaloEdgeSpec] = []
    for shard in pops:
        for sib_local, flits in shard.halo_neighbors:
            sib_global = node_to_global_idx[shard.node_id][sib_local]
            src_global = node_to_global_idx[shard.node_id][shard.shard_id]
            halo.append(HaloEdgeSpec(
                src_global_idx=src_global,
                dst_global_idx=sib_global,
                flits_per_step=int(flits),
            ))

    W = build_edge_tensor(pops, edge_specs, spike_traces, T=T, halo_edges=halo)
    return W, len(pops), len(lif_names), sum(p.size for p in pops)


def _make_edge_spec(src: str, dst: str, kind: str, meta: dict) -> EdgeSpec:
    if kind == "conv2d":
        K_kernel = int(meta.get("K", 3))
        return EdgeSpec(
            src=src, dst=dst, kind="conv2d",
            mask_factory=lambda src_pop, dst_pop, K=K_kernel: mask_conv2d(
                K=K, C_out_shard=_dst_channel_count(dst_pop),
            ),
        )
    if kind == "linear":
        return EdgeSpec(
            src=src, dst=dst, kind="linear",
            mask_factory=lambda src_pop, dst_pop: mask_linear(dst_pop.size),
        )
    return EdgeSpec(
        src=src, dst=dst, kind="identity",
        mask_factory=lambda src_pop, dst_pop: mask_identity(),
    )


def _dst_channel_count(dst_pop: MicroPopulation) -> int:
    if dst_pop.kind == "fmap":
        c_lo, c_hi = dst_pop.meta.get("c_range", (0, 1))
        return max(1, int(c_hi - c_lo))
    return max(1, dst_pop.size)


def extract(dataset: str, T: int, batch_size: int, img_size: int,
            checkpoint: str | None, data_dir: Path | None,
            num_batches: int, depths: int, embed_dim: int,
            num_heads: int, N_core_cap: int) -> Fingerprint:
    net = build_network(dataset, T, img_size, depths=depths,
                        embed_dim=embed_dim, num_heads=num_heads)
    if checkpoint:
        load_checkpoint(net, checkpoint)
    if data_dir is not None:
        loader = make_cifar_loader(data_dir, dataset, batch_size, img_size, num_batches)
        used_batches = len(loader)
    else:
        _, _, in_channels, _, _ = DATASET_CFG[dataset]
        loader = make_dummy_loader(batch_size, img_size, in_channels)
        used_batches = 1

    W, V_prime, V_orig, pop_neuron_count = build_W_for_spikformer(
        net, loader, T=T, batches=used_batches, N_core_cap=N_core_cap,
    )

    state_size_mb = sum(p.numel() * p.element_size() for p in net.parameters()) / 1e6
    fp_core = extract_fingerprint_from_W(
        W, neuron_count=pop_neuron_count,
        state_size_mb=state_size_mb, complexity_ratio=1.0,
    )
    meta = {
        "model": f"spikformer_{dataset}",
        "dataset": dataset,
        "T": str(T),
        "img_size": str(img_size),
        "batch_size": str(batch_size),
        "num_batches": str(used_batches),
        "depths": str(depths),
        "embed_dim": str(embed_dim),
        "num_heads": str(num_heads),
        "N_core_cap": str(N_core_cap),
        "V_prime": str(V_prime),
        "V_original": str(V_orig),
        "checkpoint": checkpoint or "",
        "data_dir": str(data_dir) if data_dir else "",
        "source": "fingerprint.extract_spikformer",
    }
    return Fingerprint(
        traffic_sequence=fp_core.traffic_sequence,
        global_burstiness=fp_core.global_burstiness,
        max_centrality=fp_core.max_centrality,
        mean_components=fp_core.mean_components,
        T=fp_core.T,
        neuron_count=fp_core.neuron_count,
        state_size_mb=fp_core.state_size_mb,
        complexity_ratio=fp_core.complexity_ratio,
        compute_sequence=fp_core.compute_sequence,
        centrality_var=fp_core.centrality_var,
        meta=meta,
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dataset", choices=list(DATASET_CFG), default="cifar10")
    p.add_argument("--T", type=int, default=4)
    p.add_argument("--batch-size", type=int, default=8)
    p.add_argument("--img-size", type=int, default=None)
    p.add_argument("--depths", type=int, default=4)
    p.add_argument("--embed-dim", type=int, default=384)
    p.add_argument("--num-heads", type=int, default=12)
    p.add_argument("--data-dir", type=Path, default=None,
                   help="CIFAR root (torchvision will download here if missing) or a "
                        "raw test_batch pickle file. If omitted, uses random dummy input.")
    p.add_argument("--num-batches", type=int, default=1)
    p.add_argument("--checkpoint", default=None)
    p.add_argument("--core-cap", type=int, default=4096,
                   help="Per-core neuron capacity N_core_cap for slicing (§2.2). "
                        "v3 default 4096; pass 1024 for paper §2.3.5 quick-table parity.")
    p.add_argument("--out", required=True, type=Path)
    args = p.parse_args(argv)

    img_size = args.img_size if args.img_size is not None else DATASET_CFG[args.dataset][0]
    fp = extract(
        args.dataset, args.T, args.batch_size, img_size,
        args.checkpoint, args.data_dir, args.num_batches,
        args.depths, args.embed_dim, args.num_heads, args.core_cap,
    )
    save_fingerprint(args.out, fp)
    print(f"saved {args.out}: T={fp.T} V'={fp.max_centrality.shape[0]} "
          f"beta={fp.global_burstiness:.3f} K_mean={fp.mean_components:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
