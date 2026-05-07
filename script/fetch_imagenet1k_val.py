"""Download ILSVRC/imagenet-1k validation set ONE PARQUET SHARD AT A TIME via
huggingface_hub, keep one image per class -> dataset/imagenet/val/<wnid>/<NNNNN>.JPEG.

Avoids `datasets.load_dataset` (which OOMs in this env). Uses pyarrow batch
iteration so only a small slice of each shard is in RAM at a time. Stops as
soon as all 1000 wnids are covered.

Requires HF login + accepted license at
    https://huggingface.co/datasets/ILSVRC/imagenet-1k
"""
from __future__ import annotations

import argparse
import io
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = REPO_ROOT / "dataset" / "imagenet" / "val"
REPO_ID = "ILSVRC/imagenet-1k"
NUM_SHARDS = 14
NUM_CLASSES = 1000


def load_int2wnid() -> list[str]:
    """Fetch the label_id -> wnid mapping from the dataset card metadata."""
    from huggingface_hub import hf_hub_download
    # The dataset ships a classes.py / dataset_infos.json with the names.
    # Easiest stable source: the README has it, but the canonical mapping
    # lives in the `imagenet-1k` HF dataset script. Use datasets.Features
    # via a tiny local read of one parquet's schema metadata if present;
    # otherwise fall back to the well-known wnid list shipped with timm.
    try:
        info_path = hf_hub_download(
            repo_id=REPO_ID, filename="classes.py", repo_type="dataset")
        ns: dict = {}
        exec(Path(info_path).read_text(), ns)  # noqa: S102
        names = ns.get("IMAGENET2012_CLASSES")
        if isinstance(names, dict) and len(names) == NUM_CLASSES:
            return list(names.keys())
    except Exception:
        pass
    raise RuntimeError(
        "Could not load wnid list from HF dataset; ensure classes.py is present.")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    p.add_argument("--per-class", type=int, default=1)
    p.add_argument("--keep-shards", action="store_true",
                   help="Don't delete parquet shards after processing.")
    args = p.parse_args(argv)

    import pyarrow.parquet as pq
    from huggingface_hub import hf_hub_download

    args.out.mkdir(parents=True, exist_ok=True)

    int2wnid = load_int2wnid()
    assert len(int2wnid) == NUM_CLASSES

    have: dict[str, int] = {}
    for sub in args.out.iterdir():
        if sub.is_dir():
            have[sub.name] = sum(1 for _ in sub.glob("*.JPEG"))

    target = args.per_class
    covered = sum(1 for w in int2wnid if have.get(w, 0) >= target)
    print(f"start: {covered}/{NUM_CLASSES} classes already covered")

    written = 0
    done = covered >= NUM_CLASSES

    from PIL import Image

    for shard_idx in range(NUM_SHARDS):
        if done:
            break
        fname = f"data/validation-{shard_idx:05d}-of-{NUM_SHARDS:05d}.parquet"
        print(f"[shard {shard_idx+1}/{NUM_SHARDS}] downloading {fname}")
        local = hf_hub_download(repo_id=REPO_ID, filename=fname, repo_type="dataset")

        pf = pq.ParquetFile(local)
        for batch in pf.iter_batches(batch_size=64, columns=["image", "label"]):
            labels = batch.column("label").to_pylist()
            images = batch.column("image").to_pylist()  # struct: {bytes, path}
            for lab, img in zip(labels, images):
                wnid = int2wnid[lab]
                if have.get(wnid, 0) >= target:
                    continue
                cls_dir = args.out / wnid
                cls_dir.mkdir(exist_ok=True)
                idx = have.get(wnid, 0)
                out_path = cls_dir / f"{idx:05d}.JPEG"
                data = img["bytes"] if isinstance(img, dict) else img
                Image.open(io.BytesIO(data)).convert("RGB").save(
                    out_path, format="JPEG", quality=95)
                have[wnid] = idx + 1
                written += 1
                if written % 100 == 0:
                    cov = sum(1 for w in int2wnid if have.get(w, 0) >= target)
                    print(f"  progress: {cov}/{NUM_CLASSES} classes, written={written}")
            cov = sum(1 for w in int2wnid if have.get(w, 0) >= target)
            if cov >= NUM_CLASSES:
                done = True
                break

        if not args.keep_shards:
            try:
                Path(local).unlink()
            except OSError:
                pass

    covered = sum(1 for w in int2wnid if have.get(w, 0) >= target)
    print(f"done: written={written} classes_covered={covered}/{NUM_CLASSES}")
    short = [w for w in int2wnid if have.get(w, 0) < target]
    if short:
        print(f"WARN: {len(short)} classes still short, first few: {short[:5]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
