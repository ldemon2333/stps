# Fingerprint NPZ Index

Per-model fingerprint files produced by `fingerprint/extract_*.py`.
Schema (11 keys) defined in [docs/fingerprint.md](../docs/fingerprint.md) §7:
`traffic_sequence, global_burstiness, max_centrality, mean_components, T,
neuron_count, state_size_mb, complexity_ratio, compute_sequence,
centrality_var, meta`.

All current files use the v2 spike-count path from
[docs/traffic_TODO.md](../docs/traffic_TODO.md): `traffic_sequence` is the
val-set sample mean of per-tick total spike counts across all LIF nodes
(`spikes / single inference`), under the single-card deployment assumption.
This implies `mean_components = 1.0`, uniform `max_centrality = 1/V`, and
all-zero `compute_sequence` — the spatial fields degenerate by design.

---

## spikformer_cifar10.npz

- **Source model:** [model/STEP/cls/models/static/spikformer_cifar.py](../model/STEP/cls/models/static/spikformer_cifar.py) — `Spikformer` (SPS stem + 4 SSA Blocks, depths=4, embed_dim=384, num_heads=12)
- **Checkpoint:** [model/STEP/spikformer_cifar_pth.tar](../model/STEP/spikformer_cifar_pth.tar)
- **Dataset:** [dataset/cifar-10-batches-py/test_batch](../dataset/cifar-10-batches-py/test_batch) — CIFAR-10 raw test pickle
- **Calibration sample:** `batch_size=8 × num_batches=4` = **32 images**
- **Image size:** 32×32, **T:** 4 ticks
- **Param size (state_size_mb):** **37.30 MB**

| Field | Value |
| --- | --- |
| `T` | 4 |
| `neuron_count` | 1,449,984 |
| `traffic_sequence` E^(t) | `[82070.5, 131413.6, 113852.2, 124854.5]` |
| `traffic_sequence.sum()` | 4.522e+05 spikes |
| `global_burstiness` β | **1.1625** |
| `mean_components` K̄ | 1.0 (degenerate) |
| `max_centrality` | uniform 1/V ≈ 6.897e-07 |
| `compute_sequence` | all zeros (v2) |

```bash
/root/miniconda3/envs/snn/bin/python -m fingerprint.extract_spikformer \
    --dataset cifar10 --T 4 \
    --checkpoint model/STEP/spikformer_cifar_pth.tar \
    --data-dir dataset/cifar-10-batches-py/test_batch \
    --batch-size 8 --num-batches 4 \
    --out npz/spikformer_cifar10.npz
```

---

## qkformer_cifar10.npz

- **Source model:** [model/STEP/cls/models/static/qkformer_cifar.py](../model/STEP/cls/models/static/qkformer_cifar.py) — `QKFormer` (3 patch_embeds + stage1/stage2 TokenSpikingTransformer + stage3 SpikingTransformer ×2, depths=4, embed_dim=384, num_heads=12)
- **Checkpoint:** [model/QKFormer_cifa10.tar](../model/QKFormer_cifa10.tar)
- **Dataset:** [dataset/cifar-10-batches-py/test_batch](../dataset/cifar-10-batches-py/test_batch)
- **Calibration sample:** `batch_size=8 × num_batches=4` = **32 images**
- **Image size:** 32×32, **T:** 4 ticks
- **Param size (state_size_mb):** **26.83 MB**

| Field | Value |
| --- | --- |
| `T` | 4 |
| `neuron_count` | 2,369,536 |
| `traffic_sequence` E^(t) | `[186893.3, 258379.5, 238449.8, 245573.3]` |
| `traffic_sequence.sum()` | 9.293e+05 spikes |
| `global_burstiness` β | **1.1122** |
| `mean_components` K̄ | 1.0 (degenerate) |
| `max_centrality` | uniform 1/V ≈ 4.220e-07 |
| `compute_sequence` | all zeros (v2) |

```bash
/root/miniconda3/envs/snn/bin/python -m fingerprint.extract_spikformer \
    --model qkformer --dataset cifar10 --T 4 \
    --checkpoint model/QKFormer_cifa10.tar \
    --data-dir dataset/cifar-10-batches-py/test_batch \
    --batch-size 8 --num-batches 4 \
    --out npz/qkformer_cifar10.npz
```

---

## spikingresformer_ti_imagenet.npz

- **Source model:** [model/spikingresformer/network.py](../model/spikingresformer/network.py) — `spikingresformer_ti` (SpikingJelly multi-step, T=4)
- **Checkpoint:** [model/spikingresformer/spikingresformer_ti.pth](../model/spikingresformer/spikingresformer_ti.pth)
- **Dataset:** [dataset/imagenet/val](../dataset/imagenet/val) — ImageFolder layout
- **Calibration sample:** `batch_size=4 × num_batches=4` = **16 images**
- **Image size:** 224×224, **T:** 4 ticks
- **Param size (state_size_mb):** **44.72 MB**

| Field | Value |
| --- | --- |
| `T` | 4 |
| `neuron_count` | 11,180,992 (parameter-count proxy) |
| `traffic_sequence` E^(t) | `[1.7365e6, 1.7571e6, 1.8261e6, 1.8197e6]` |
| `traffic_sequence.sum()` | 7.140e+06 spikes |
| `global_burstiness` β | **1.0231** |
| `mean_components` K̄ | 1.0 (degenerate) |
| `max_centrality` | uniform 1/V ≈ 8.944e-08 |
| `compute_sequence` | all zeros (v2) |

```bash
/root/miniconda3/envs/snn/bin/python -m fingerprint.extract_spikingresformer \
    --variant ti --dataset imagenet --T 4 --img-size 224 \
    --checkpoint model/spikingresformer/spikingresformer_ti.pth \
    --data-dir dataset/imagenet/val \
    --batch-size 4 --num-batches 4 \
    --out npz/spikingresformer_ti_imagenet.npz
```

---

## Side-by-side

| Metric | spikformer_cifar10 | qkformer_cifar10 | spikingresformer_ti_imagenet |
| --- | --- | --- | --- |
| Param size (MB) | 37.30 | 26.83 | 44.72 |
| neuron_count | 1.45 M | 2.37 M | 11.18 M |
| Calibration images | 32 | 32 | 16 |
| Spikes / inference (sum E^(t)) | 4.52e+05 | 9.29e+05 | 7.14e+06 |
| β (burstiness) | 1.1625 | 1.1122 | 1.0231 |

β orders the three workloads from peakiest (Spikformer) to most uniform
(SpikingResformer-Ti). Under the v2 single-card simplification, β and the
E^(t) timeline are the load-bearing fingerprints; K̄, `max_centrality`, and
`compute_sequence` are kept in the schema only for back-compatibility.

---

## Synthetic fingerprints

Produced by `fingerprint/synth.py` via `fingerprint.cli`. Calibrated to the
same `spikes / single inference` scale as the real-model fingerprints
(`E.mean() ≈ 2e5`, `neuron_count = 2,000,000`, `state_size_mb = 32`) so they
can be mixed into the same Q0/Q1 fingerprint directory without one source
dominating the per-tick load. β is exact: the generator places one tick at
`H = β(T−1)/(T−β)` and the rest at 1, then rescales to the target `E.mean()`.

| File | T | β | E^(t) | Use case |
|---|---|---|---|---|
| [synthetic_flat.npz](synthetic_flat.npz) | 4 | **1.050** | `[1.97e5, 2.10e5, 1.97e5, 1.97e5]` | Near-uniform load,基线 Step B 不会有收益 |
| [synthetic_pulse_t8.npz](synthetic_pulse_t8.npz) | 8 | **1.800** | 7 tick 取 1.77e5,第 2 tick 单脉冲 3.60e5 | 中短窗口窄脉冲,Step B 收益中等 |
| [synthetic_pulse_t16.npz](synthetic_pulse_t16.npz) | 16 | **2.500** | 15 tick 取 1.80e5,第 4 tick 单脉冲 5.00e5 | 长窗口窄脉冲,Step B 削峰收益典型 |
| [synthetic_bursty.npz](synthetic_bursty.npz) | 4 | **3.800** | `[1.33e4, 7.60e5, 1.33e4, 1.33e4]` | 激进突发,单 tick 占 95% 流量,Step B 价值最大化 |

```bash
python -m fingerprint.cli --synthetic --T 4 --beta 1.05 --K 1 \
    --neuron-count 2000000 --state-size-mb 32 --e-mean 200000 --seed 21 \
    --out npz/synthetic_flat.npz
python -m fingerprint.cli --synthetic --T 8 --beta 1.8 --K 1 \
    --neuron-count 2000000 --state-size-mb 32 --e-mean 200000 --seed 21 \
    --out npz/synthetic_pulse_t8.npz
python -m fingerprint.cli --synthetic --T 16 --beta 2.5 --K 1 \
    --neuron-count 2000000 --state-size-mb 32 --e-mean 200000 --seed 21 \
    --out npz/synthetic_pulse_t16.npz
python -m fingerprint.cli --synthetic --T 4 --beta 3.8 --K 1 \
    --neuron-count 2000000 --state-size-mb 32 --e-mean 200000 --seed 21 \
    --out npz/synthetic_bursty.npz
```

`--beta` 严格区间 `[1, T·0.95]`(超过会被 clip),`--e-mean` 控制 `E.mean()`,
`--seed` 决定脉冲落在哪个 tick。`K̄`、`max_centrality`、`compute_sequence`
在单卡假设下都退化,只有 β 和 E^(t) 实际承载信息。
