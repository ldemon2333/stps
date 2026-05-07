#!/bin/bash
#
# Stream ILSVRC2012 val from HF (ILSVRC/imagenet-1k) and keep ONE image per
# class -> dataset/imagenet/val/<wnid>/<filename>.JPEG (1000 wnids total).
#
# Requires: `huggingface-cli login` and accepted license at
#   https://huggingface.co/datasets/ILSVRC/imagenet-1k
#
set -euo pipefail
cd "$(dirname "$0")/.."
exec /root/miniconda3/envs/snn/bin/python script/fetch_imagenet1k_val.py "$@"
