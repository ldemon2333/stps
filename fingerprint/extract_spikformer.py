"""Adapter: turn a STEP/Spikformer instance into a v3 fingerprint .npz.

Implements docs/traffic_TODO.md spike-count simplification: under the
single-card-deployment assumption, E^(t) reduces to the val-set sample mean
of per-tick spike counts across all LIF nodes.

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
from typing import Iterable, NamedTuple

import numpy as np

from . import (
    Fingerprint,
    extract_fingerprint_from_spikes,
    save_fingerprint,
)

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
                  embed_dim: int = 384, num_heads: int = 12,
                  model: str = "spikformer"):
    _ensure_step_on_path()
    from models.utils.node import LIFNode  # type: ignore
    from models.utils.surrogate import SigmoidGrad  # type: ignore

    _, num_classes, in_channels, _, _ = DATASET_CFG[dataset]
    if model == "qkformer":
        from models.static.qkformer_cifar import QKFormer  # type: ignore
        return QKFormer(
            step=T, img_size=img_size, patch_size=patch_size_for(img_size),
            in_channels=in_channels, num_classes=num_classes,
            embed_dim=embed_dim, num_heads=num_heads,
            mlp_ratio=4, scale=0.125, attn_drop=0.0,
            depths=depths, node=LIFNode, tau=2.0, threshold=1.0,
            act_func=SigmoidGrad, alpha=4.0, layer_by_layer=True,
        )
    from models.static.spikformer_cifar import Spikformer  # type: ignore
    return Spikformer(
        step=T, img_size=img_size, patch_size=4,
        in_channels=in_channels, num_classes=num_classes,
        embed_dim=embed_dim, num_heads=num_heads,
        mlp_ratio=4, attn_scale=0.125, attn_drop=0.0,
        depths=depths, node=LIFNode, tau=2.0, threshold=1.0,
        act_func=SigmoidGrad, alpha=4.0,
        embed_layer="SPS", attn_layer="SSA", layer_by_layer=True,
    )


def patch_size_for(img_size: int) -> int:
    return 4 if img_size <= 64 else 16


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


class SampleBatch(NamedTuple):
    images: object
    labels: object
    indices: object
    paths: list[str]


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
        indices = torch.arange(len(labels), dtype=torch.int64)
        ds = TensorDataset(x, torch.from_numpy(labels), indices)
    else:
        cls = datasets.CIFAR100 if dataset == "cifar100" else datasets.CIFAR10
        base_ds = cls(str(data_dir), train=False, download=True, transform=tfm)

        class IndexedDataset(torch.utils.data.Dataset):
            def __init__(self, dataset):
                self.dataset = dataset

            def __len__(self):
                return len(self.dataset)

            def __getitem__(self, idx):
                x, y = self.dataset[idx]
                return x, y, idx

        ds = IndexedDataset(base_ds)

    loader = DataLoader(ds, batch_size=batch_size, shuffle=False, num_workers=0)
    out = []
    for i, batch in enumerate(loader):
        if i >= num_batches:
            break
        x, y, idx = batch
        paths = [f"{dataset}/test/{int(v)}" for v in idx]
        out.append(SampleBatch(x, y, idx, paths))
    return out


def _is_spiking_neuron(module) -> bool:
    _ensure_step_on_path()
    try:
        from models.utils.node import BaseNode_Torch  # type: ignore
        return isinstance(module, BaseNode_Torch)
    except Exception:
        return type(module).__name__.endswith(("Node",))


def spike_count_timeline(net, dataloader: Iterable, T: int, batches: int) -> tuple:
    """Run forward and return mean trace plus per-sample measured traces."""
    import torch

    lif_modules = [(n, m) for n, m in net.named_modules() if _is_spiking_neuron(m)]
    if not lif_modules:
        raise ValueError("No spiking neurons found in net")

    sum_E = np.zeros(T, dtype=np.float64)
    sample_traces: list[np.ndarray] = []
    sample_indices: list[int] = []
    sample_labels: list[int] = []
    sample_paths: list[str] = []
    n_samples = 0
    batch_E = {"value": None}  # (T, B) accumulator across hooks for current batch
    current_batch_size = {"value": None}
    neuron_count = {"value": 0}

    def _hook(_mod, _inp, out):
        arr = out.detach()
        B_expected = current_batch_size["value"]
        if (
            arr.shape[0] == T
            and arr.dim() >= 2
            and B_expected is not None
            and arr.shape[1] == B_expected
        ):
            reduced = arr.flatten(2).sum(dim=2) if arr.dim() > 2 else arr
            sample_size = int(np.prod(arr.shape[2:])) if arr.dim() > 2 else 1
        elif arr.shape[0] % T == 0:
            B = arr.shape[0] // T
            reduced = arr.reshape(T, B, -1).sum(dim=2)
            sample_size = int(np.prod(arr.shape[1:]))
        else:
            raise ValueError(
                f"LIF trace leading dim {arr.shape[0]} not compatible with T={T}"
            )
        reduced_np = reduced.float().cpu().numpy().astype(np.float64)
        if batch_E["value"] is None:
            batch_E["value"] = reduced_np
        else:
            batch_E["value"] = batch_E["value"] + reduced_np
        if n_samples == 0:
            neuron_count["value"] += sample_size

    hooks = [m.register_forward_hook(_hook) for _, m in lif_modules]

    try:
        net.eval()
        with torch.no_grad():
            for b, batch in enumerate(dataloader):
                if b >= batches:
                    break
                batch_E["value"] = None
                if isinstance(batch, SampleBatch):
                    x = batch.images
                    labels = batch.labels
                    indices = batch.indices
                    paths = batch.paths
                elif isinstance(batch, (list, tuple)):
                    x = batch[0]
                    labels = batch[1] if len(batch) > 1 else torch.full((x.shape[0],), -1, dtype=torch.int64)
                    indices = batch[2] if len(batch) > 2 else torch.arange(n_samples, n_samples + x.shape[0])
                    paths = [f"sample/{int(v)}" for v in indices]
                else:
                    x = batch
                    labels = torch.full((x.shape[0],), -1, dtype=torch.int64)
                    indices = torch.arange(n_samples, n_samples + x.shape[0])
                    paths = [f"sample/{int(v)}" for v in indices]

                current_batch_size["value"] = int(x.shape[0])
                net(x)
                if batch_E["value"] is None:
                    continue
                arr = batch_E["value"]
                sum_E += arr.sum(axis=1)
                sample_traces.extend(arr.T.astype(np.float32))
                sample_indices.extend(int(v) for v in indices.cpu().numpy())
                sample_labels.extend(int(v) for v in labels.cpu().numpy())
                sample_paths.extend(paths)
                n_samples += arr.shape[1]
    finally:
        for h in hooks:
            h.remove()

    E = (sum_E / n_samples).astype(np.float32) if n_samples > 0 else np.zeros(T, dtype=np.float32)
    traces = (
        np.stack(sample_traces).astype(np.float32)
        if sample_traces
        else np.zeros((0, T), dtype=np.float32)
    )
    return (
        E,
        int(neuron_count["value"]),
        traces,
        np.asarray(sample_indices, dtype=np.int32),
        np.asarray(sample_labels, dtype=np.int32),
        np.asarray(sample_paths, dtype=str),
    )


def extract(dataset: str, T: int, batch_size: int, img_size: int,
            checkpoint: str | None, data_dir: Path | None,
            num_batches: int, depths: int, embed_dim: int,
            num_heads: int, model: str = "spikformer",
            sample_index: int = 0) -> Fingerprint:
    net = build_network(dataset, T, img_size, depths=depths,
                        embed_dim=embed_dim, num_heads=num_heads, model=model)
    if checkpoint:
        load_checkpoint(net, checkpoint)
    if data_dir is not None:
        loader = make_cifar_loader(data_dir, dataset, batch_size, img_size, num_batches)
        used_batches = len(loader)
    else:
        _, _, in_channels, _, _ = DATASET_CFG[dataset]
        loader = make_dummy_loader(batch_size, img_size, in_channels)
        used_batches = 1

    E, neuron_count, sample_traces, sample_indices, sample_labels, sample_paths = spike_count_timeline(net, loader, T=T, batches=used_batches)

    state_size_mb = sum(p.numel() * p.element_size() for p in net.parameters()) / 1e6
    meta = {
        "model": f"{model}_{dataset}",
        "dataset": dataset,
        "T": str(T),
        "img_size": str(img_size),
        "batch_size": str(batch_size),
        "num_batches": str(used_batches),
        "depths": str(depths),
        "embed_dim": str(embed_dim),
        "num_heads": str(num_heads),
        "checkpoint": checkpoint or "",
        "data_dir": str(data_dir) if data_dir else "",
        "source": "fingerprint.extract_spikformer",
    }
    fp = extract_fingerprint_from_spikes(
        E,
        neuron_count=neuron_count,
        state_size_mb=state_size_mb,
        complexity_ratio=1.0,
        meta=meta,
    )
    sample_pos = 0
    if sample_indices.shape[0]:
        matches = np.where(sample_indices == sample_index)[0]
        sample_pos = int(matches[0]) if matches.shape[0] else 0

    return Fingerprint(
        mean_injection_trace=fp.mean_injection_trace,
        global_burstiness=fp.global_burstiness,
        max_centrality=fp.max_centrality,
        mean_components=fp.mean_components,
        T=fp.T,
        neuron_count=fp.neuron_count,
        state_size_mb=fp.state_size_mb,
        complexity_ratio=fp.complexity_ratio,
        compute_sequence=fp.compute_sequence,
        centrality_var=fp.centrality_var,
        sample_measured_injection_trace=(
            sample_traces[sample_pos].astype(np.float32) if sample_traces.shape[0] else np.zeros(0, dtype=np.float32)
        ),
        sample_index=int(sample_indices[sample_pos]) if sample_indices.shape[0] else -1,
        sample_label=int(sample_labels[sample_pos]) if sample_labels.shape[0] else -1,
        sample_path=str(sample_paths[sample_pos]) if sample_paths.shape[0] else "",
        meta=fp.meta,
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
    p.add_argument("--model", choices=["spikformer", "qkformer"], default="spikformer")
    p.add_argument("--sample-index", type=int, default=0,
                   help="Dataset test index used as single-image measured ground truth.")
    p.add_argument("--out", required=True, type=Path)
    args = p.parse_args(argv)

    img_size = args.img_size if args.img_size is not None else DATASET_CFG[args.dataset][0]
    fp = extract(
        args.dataset, args.T, args.batch_size, img_size,
        args.checkpoint, args.data_dir, args.num_batches,
        args.depths, args.embed_dim, args.num_heads,
        args.model,
        args.sample_index,
    )
    save_fingerprint(args.out, fp)
    print(f"saved {args.out}: T={fp.T} neurons={fp.neuron_count} "
          f"beta={fp.global_burstiness:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
