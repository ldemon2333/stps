# Fingerprint 完整测试记录

日期：2026-05-07

本文记录 `docs/fingerprint.md` 对应的 fingerprint 特性测试。测试目标是把文档中的核心语义转成可重复的 pytest 契约：硬件感知切分、NoC mask、边张量构建、图指纹提取、`.npz` schema round-trip、CLI 与 STPS 读端消费。

## 环境

- 工作目录：`/root/v3`
- Python：`/root/miniconda3/envs/snn/bin/python`
- 测试框架：`pytest`
- 主要代码：`fingerprint/`、`schedule/stps.py`、`util/task.py`

## 测试矩阵

| 文档章节 | 语义 | 测试文件 |
|---|---|---|
| §2.3 | LIF 节点 hardware-aware slicing | `tests/test_fingerprint_slicing.py` |
| §3 / §4 | Traffic / Compute 二通道边张量，mask A/B/C/I，delay，halo | `tests/test_fingerprint_contract.py` |
| §5 | $E^{(t)}$、$\beta$、$\bar K$、$\mathbf{c}^{*}_{\max}$ 提取 | `tests/test_fingerprint_pipeline.py`、`tests/test_fingerprint_contract.py` |
| §6 | 样本维取期望，避免 batch sum 膨胀 | `tests/test_fingerprint_contract.py` |
| §7 | `.npz` schema save/load/save | `tests/test_fingerprint_pipeline.py`、`tests/test_fingerprint_contract.py` |
| §7.1 | STPS 读端消费 fingerprint 字段 | `tests/test_stps_scheduler.py`、`tests/test_simulation_integration.py` |
| §9 | 全零图、`T=0` 退化分支 | `tests/test_fingerprint_contract.py` |

## Import sanity

命令：

```bash
/root/miniconda3/envs/snn/bin/python - <<'PY'
from fingerprint import Fingerprint, split_layer, save_fingerprint, load_fingerprint
print('import_ok', Fingerprint.__name__, split_layer.__name__, save_fingerprint.__name__, load_fingerprint.__name__)
pops = split_layer('attn_lif', 'token_embed', (64, 384), N_core_cap=1024, head_dim=32)
print('attn_lif_shards', len(pops))
print('attn_lif_first_last', pops[0].meta['c_range'], pops[-1].meta['c_range'])
print('attn_lif_sizes_unique', sorted({p.size for p in pops}))
PY
```

输出：

```text
import_ok Fingerprint split_layer save_fingerprint load_fingerprint
attn_lif_shards 12
attn_lif_first_last (0, 32) (352, 384)
attn_lif_sizes_unique [2048]
```

## Slicing 测试

新增 `tests/test_fingerprint_slicing.py` 覆盖三类节点：

- `vec`：`fc1_lif (1536,)` 在 `N_core_cap=1024` 下切成 `[1024, 512]`。
- `fmap` 且 `S <= N_core_cap`：channel-first 切分，`proj_lif` 得 48 shards，`proj_lif3` 得 24 shards，无 halo。
- `fmap` 且 `S > N_core_cap`：`(1,56,56)` 切 4 条 row strip，并注册双向 halo。
- `token_embed`：`attn_lif (64,384)` 在 `N_core_cap=1024, head_dim=32` 下严格得到 12 shards。

`attn_lif` 公式复核：

$$
C_p = \max\left(d_{\text{head}},\left\lfloor\frac{N_{\text{core\_cap}}}{N_{\text{tok}}}\right\rfloor // d_{\text{head}} \cdot d_{\text{head}}\right)
$$

$$
N_{\text{core\_cap}}=1024,\quad N_{\text{tok}}=64,\quad d_{\text{head}}=32
$$

$$
\left\lfloor\frac{1024}{64}\right\rfloor=16,\quad 16 // 32 \cdot 32=0,\quad C_p=32
$$

$$
\left\lceil\frac{384}{32}\right\rceil=12
$$

命令与结果：

```bash
/root/miniconda3/envs/snn/bin/python -m pytest tests/test_fingerprint_slicing.py -q
```

```text
.....                                                                    [100%]
5 passed in 0.03s
```

## Contract 测试

新增 `tests/test_fingerprint_contract.py`，把 `docs/fingerprint.md` 的公式语义转为小型确定性输入：

- `mask_linear`、`mask_conv2d`、`mask_pruned`、`mask_identity` 匹配 §4 A/B/C/I 闭式公式。
- `build_edge_tensor` 对 `(T,B,U)` spike trace 沿 batch 维取均值，不做 batch sum；同时生成 Traffic 与 Compute 二通道。
- `EdgeSpec.delta` 做因果 zero-padding 延迟。
- halo 边按几何 flits 产生 memory traffic，Compute 通道为 0。
- `extract_fingerprint_from_W` 聚合 Traffic / Compute，支持 `edge_threshold` 过滤连通分量，保留 time-global max centrality。
- 全零图返回 `beta=1`、`K=0`、均匀中心性。
- `T=0` 返回空 `traffic_sequence` / `compute_sequence`、`K=0`，不崩溃。
- `save -> load -> save -> load` 保留 schema 数值与 metadata。

命令与结果：

```bash
/root/miniconda3/envs/snn/bin/python -m pytest tests/test_fingerprint_contract.py -q
```

```text
.......                                                                  [100%]
7 passed in 0.05s
```

本轮测试发现并修复了一个边界问题：`extract_fingerprint_from_W(np.zeros((0,V,V)))` 原先会在 `reshape(0, -1)` 报错；已改为 `sum(axis=(1, 2))`，符合 §9 的 `T=0` 退化分支描述。

## Pipeline / IO / CLI 测试

`tests/test_fingerprint_pipeline.py` 覆盖：

- 当前 `.npz` schema round-trip。
- in-eigenvector centrality 汇点集中与全零图均匀退化。
- extractor 对 burst 与 active connected components 的识别。
- 非法 W shape 拒绝。
- synthetic fingerprint 确定性。
- `python -m fingerprint.cli` 生成可加载 `.npz`。

命令与结果：

```bash
/root/miniconda3/envs/snn/bin/python -m pytest tests/test_fingerprint_pipeline.py -q
```

```text
.......                                                                  [100%]
7 passed in 0.23s
```

## Fingerprint 组合测试

命令与结果：

```bash
/root/miniconda3/envs/snn/bin/python -m pytest tests/test_fingerprint_pipeline.py tests/test_fingerprint_slicing.py tests/test_fingerprint_contract.py -q
```

```text
...................                                                      [100%]
19 passed in 0.25s
```

## STPS 读端回归

文档 §7.1 说明 STPS 在线阶段只消费落盘 fingerprint 字段。覆盖点：

- `traffic_sequence` 驱动 forecast 与 phase shift。
- `global_burstiness` 更新 card burstiness EMA。
- `mean_components` 参与 Stage 1 fragmentation match。
- `max_centrality` 参与 Stage 3 split plan。
- simulation 中 task traffic 按 fingerprint 时间窗消耗且不 wrap。

命令与结果：

```bash
/root/miniconda3/envs/snn/bin/python -m pytest tests/test_stps_scheduler.py tests/test_simulation_integration.py -q
```

```text
.................                                                        [100%]
17 passed in 0.41s
```

## 全量回归

命令与结果：

```bash
/root/miniconda3/envs/snn/bin/python -m pytest tests -q
```

```text
.............................................                            [100%]
45 passed in 0.65s
```

## 结论

- `docs/fingerprint.md` 描述的核心代码路径已有完整单元/契约/集成覆盖。
- 当前代码严格按 §2.3.3 公式实现 `token_embed` 切分；`attn_lif` 在 `N_core_cap=1024, N_tok=64, d_head=32, C=384` 下是 12 shards/block。
- `docs/fingerprint.md` §2.3.5 的 `attn_lif C_p=384 / 4 shards` 快表仍与严格公式不一致；本轮仍未改原文档表格，只在测试文档中记录代码与公式真值。
- `T=0` 退化分支已由测试覆盖并修复。
