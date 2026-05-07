# dataset/

Datasets used by v3 fingerprint extraction. Not checked into git
(see `.gitignore`); reproducible via the scripts under `script/`.

## ImageNet-1k (val, 1 image per class)

Layout expected by `fingerprint/extract_spikingresformer.py --data-dir`,
matching the SpikingResformer readme:

```
dataset/imagenet/val/<wnid>/*.JPEG
```

1000 wnid directories (`n01440764`, `n01443537`, ...), 1 image each,
1000 images total.

### Fetching the val images

We pull from the Hugging Face dataset
[`ILSVRC/imagenet-1k`](https://huggingface.co/datasets/ILSVRC/imagenet-1k)
(gated — requires `huggingface-cli login` and accepting the license on the
dataset page). The downloader streams parquet shards via `huggingface_hub` +
`pyarrow` (one shard at a time, deleted after processing) to avoid the OOM
that `datasets.load_dataset` triggers in this environment. It stops as soon
as all 1000 wnids are covered, so only the first ~3 of 14 shards are
actually downloaded.

```bash
pip install huggingface_hub pyarrow pillow
huggingface-cli login                              # once
python script/fetch_imagenet1k_val.py              # writes to dataset/imagenet/val/
python script/fetch_imagenet1k_val.py --per-class 5    # 5 images per class
bash script/extract_ILSVRC.sh                      # thin wrapper using the project python
```

The wnid → human-readable-name mapping is loaded from `classes.py` in the
HF dataset repo, so directory names always match the canonical ILSVRC2012
synset IDs.

### License

ImageNet imagery is provided under the original ImageNet research license
(non-commercial). By running the fetch script you accept those terms.
