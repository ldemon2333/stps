"""Adapter: turn a SpikingResformer instance into a v3 fingerprint .npz.

Implements docs/fingerprint.md §6 for SpikingResformer (CVPR 2024). Unlike
the STEP path, SpikingResformer uses SpikingJelly multi-step convention
(leading T axis preserved). We delegate topology discovery to the generic
DTDGBuilder which already handles SpikingJelly hooks + auto-edge detection.

CLI:
    python -m fingerprint.extract_spikingresformer \
        --variant ti --dataset cifar --T 4 \
        --out npz/spikingresformer_ti_cifar.npz

    python -m fingerprint.extract_spikingresformer \
        --variant s --dataset imagenet --img-size 224 --T 4 \
        --data-dir dataset/imagenet/val --batch-size 8 --num-batches 4 \
        --out npz/spikingresformer_s_imagenet.npz
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

from . import Fingerprint, extract_fingerprint_from_W, save_fingerprint
from .dtdg import DTDGBuilder

VARIANTS = ("ti", "s", "m", "l", "dvsg", "cifar")
DATASET_SHAPES = {
    "cifar": (32, 10, 3),
    "cifar100": (32, 100, 3),
    "imagenet": (224, 1000, 3),
    "dvsg": (128, 11, 3),
}


def build_network(variant: str, dataset: str, T: int, img_size: int):
    import torch  # noqa: F401
    from spikingjelly.activation_based import surrogate, neuron
    from model.spikingresformer import layers as sr_layers
    from model.spikingresformer import network as resformer

    # Patch upstream IF/LIF/PLIF subclasses to drop hard-coded cupy backend.
    if not getattr(sr_layers.IF.__init__, "_v3_patched", False):
        def _if_init(self):
            neuron.IFNode.__init__(
                self, v_threshold=1., v_reset=0.,
                surrogate_function=surrogate.ATan(), detach_reset=True,
                step_mode="m", backend="torch", store_v_seq=False,
            )
        _if_init._v3_patched = True
        sr_layers.IF.__init__ = _if_init

        def _lif_init(self):
            neuron.LIFNode.__init__(
                self, tau=2., decay_input=True, v_threshold=1., v_reset=0.,
                surrogate_function=surrogate.ATan(), detach_reset=True,
                step_mode="m", backend="torch", store_v_seq=False,
            )
        _lif_init._v3_patched = True
        sr_layers.LIF.__init__ = _lif_init

        def _plif_init(self):
            neuron.ParametricLIFNode.__init__(
                self, init_tau=2., decay_input=True, v_threshold=1., v_reset=0.,
                surrogate_function=surrogate.ATan(), detach_reset=True,
                step_mode="m", backend="torch", store_v_seq=False,
            )
        _plif_init._v3_patched = True
        sr_layers.PLIF.__init__ = _plif_init

    factory = getattr(resformer, f"spikingresformer_{variant}")
    _, num_classes, _ = DATASET_SHAPES[dataset]
    return factory(img_size=img_size, num_classes=num_classes, T=T)


def load_checkpoint(net, ckpt_path: str) -> None:
    import torch
    state = torch.load(ckpt_path, map_location="cpu")
    if isinstance(state, dict) and "model" in state:
        state = state["model"]
    net.load_state_dict(state, strict=False)


IMAGENET_MEAN = (0.485, 0.456, 0.406)
IMAGENET_STD = (0.229, 0.224, 0.225)


def make_dummy_loader(batch_size: int, img_size: int, in_channels: int):
    import torch
    return [(torch.randn(batch_size, in_channels, img_size, img_size),)]


def make_imagefolder_loader(data_dir: Path, batch_size: int, img_size: int, num_batches: int):
    import torch  # noqa: F401
    from torch.utils.data import DataLoader
    from torchvision import datasets, transforms

    tfm = transforms.Compose([
        transforms.Resize(img_size),
        transforms.CenterCrop(img_size),
        transforms.ToTensor(),
        transforms.Normalize(IMAGENET_MEAN, IMAGENET_STD),
    ])
    ds = datasets.ImageFolder(str(data_dir), transform=tfm)
    loader = DataLoader(ds, batch_size=batch_size, shuffle=False, num_workers=0)
    out = []
    for i, batch in enumerate(loader):
        if i >= num_batches:
            break
        out.append(batch)
    return out


def extract(
    variant: str,
    dataset: str,
    T: int,
    batch_size: int,
    img_size: int,
    checkpoint: str | None,
    data_dir: Path | None,
    num_batches: int,
    N_core_cap: int,
) -> Fingerprint:
    net = build_network(variant, dataset, T, img_size)
    if checkpoint:
        load_checkpoint(net, checkpoint)
    if data_dir is not None:
        loader = make_imagefolder_loader(data_dir, batch_size, img_size, num_batches)
        used_batches = len(loader)
    else:
        _, _, in_channels = DATASET_SHAPES[dataset]
        loader = make_dummy_loader(batch_size, img_size, in_channels)
        used_batches = 1

    W = DTDGBuilder.from_spikingjelly(
        net, loader, T=T, batches=used_batches, N_core_cap=N_core_cap,
    )

    state_size_mb = sum(p.numel() * p.element_size() for p in net.parameters()) / 1e6
    V_prime = int(W.shape[1])
    fp_core = extract_fingerprint_from_W(
        W, neuron_count=V_prime,
        state_size_mb=state_size_mb, complexity_ratio=1.0,
    )
    meta = {
        "model": f"spikingresformer_{variant}",
        "dataset": dataset,
        "T": str(T),
        "img_size": str(img_size),
        "batch_size": str(batch_size),
        "num_batches": str(used_batches),
        "N_core_cap": str(N_core_cap),
        "V_prime": str(V_prime),
        "checkpoint": checkpoint or "",
        "data_dir": str(data_dir) if data_dir else "",
        "source": "fingerprint.extract_spikingresformer",
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
    p.add_argument("--variant", choices=VARIANTS, required=True)
    p.add_argument("--dataset", choices=list(DATASET_SHAPES), default="cifar")
    p.add_argument("--T", type=int, default=4)
    p.add_argument("--batch-size", type=int, default=2)
    p.add_argument("--img-size", type=int, default=None)
    p.add_argument("--data-dir", type=Path, default=None,
                   help="ImageFolder root (e.g. dataset/imagenet/val). "
                        "If omitted, uses random dummy input.")
    p.add_argument("--num-batches", type=int, default=1)
    p.add_argument("--checkpoint", default=None)
    p.add_argument("--core-cap", type=int, default=4096)
    p.add_argument("--out", required=True, type=Path)
    args = p.parse_args(argv)

    img_size = args.img_size if args.img_size is not None else DATASET_SHAPES[args.dataset][0]
    fp = extract(
        args.variant, args.dataset, args.T, args.batch_size,
        img_size, args.checkpoint, args.data_dir, args.num_batches,
        args.core_cap,
    )
    save_fingerprint(args.out, fp)
    print(f"saved {args.out}: T={fp.T} V'={fp.max_centrality.shape[0]} "
          f"beta={fp.global_burstiness:.3f} K_mean={fp.mean_components:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
