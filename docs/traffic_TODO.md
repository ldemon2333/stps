# Fingerprint ② — 流量序列 `E^(t)` 简化方案 TODO

## 1. 背景与目标

当前 `docs/fingerprint.md` §5.2 把 $E^{(t)}$ 定义为"全图聚合期望流量",
依赖一个完整的 $(T, V', V', 2)$ DTDG 边权张量:

$$
E^{(t)} \;=\; \sum_{i=1}^{V'}\sum_{j=1}^{V'} \mathrm{Traffic}^{(t)}_{ij}
$$

这套流水线需要:

- §2.3 硬件感知切分(`slicing.py` / `MicroPopulation`)。
- §4 突触掩码 $\mathbf{M}_{ij}$(`mask.py` 三个 case)。
- §3.2 跨样本期望 + halo 边(`edge_builder.py` / `dtdg.py`)。

**新约束:** 单模型不允许跨卡部署 — 整个模型一定完整落在单张物理卡上。
因此 STPS Step B 所关心的"卡内 NoC 时间潮汐"可以脱离边粒度,直接
退化为 **整模型每个 tick 的脉冲发放总数**。

**新定义(目标语义):**

记 val 数据集为 $\mathcal{D}_{\text{val}}=\{b_1,\dots,b_N\}$,
$N=\lvert\mathcal{D}_{\text{val}}\rvert$。对单个样本 $b$:

$$
E^{(t)}_b \;=\; \sum_{i=1}^{V} \bigl\lVert
\mathbf{x}^{(t)}_{i,\text{spike},b}
\bigr\rVert_0
\quad(\text{该样本在 tick }t\text{ 上全模型的脉冲发放总数})
$$

最终落盘的 $E^{(t)}$ 是**整个 val 集**上的逐样本算术平均:

$$
\boxed{\;
E^{(t)} \;=\; \frac{1}{N}\sum_{b=1}^{N} E^{(t)}_b
\;=\; \frac{1}{N}\sum_{b=1}^{N}\sum_{i=1}^{V}
\bigl\lVert \mathbf{x}^{(t)}_{i,\text{spike},b}\bigr\rVert_0
\;}
$$

也就是,对模型里每一个脉冲神经元 module(无需再切片),在每个 tick 数
"有多少个神经元发放了脉冲",对**整个 val 集**所有样本求平均(批次只是
分块送进 GPU 的实现手段,统计语义不是"per-batch 期望"而是"整个数据集
的样本均值"),再跨所有 LIF 节点求和,得到一条长度为 $T$ 的一维数组。

$E^{(t)}$ 仍是"单样本推理"语义(样本维取过期望),$\beta$ 仍按峰均比
计算,Step B 的互相关流水线无须改动 — 改动只发生在 *如何生成
`traffic_sequence`*。

## 2. 受影响的文件 / 模块清单

实现 + 文档 + 测试:

- `fingerprint/extractor.py` — 增加新入口或重写 `extract_fingerprint_from_W`
  使其可接受 spike-count 时间线作为输入。
- `fingerprint/dtdg.py` — `from_spikingjelly` 当前构造的是 $(T,V',V',2)$
  边张量,新流程只需要 spike 计数,可以提供一个轻量旁路 `spike_count_timeline_from_spikingjelly`。
- `fingerprint/extract_spikformer.py`, `fingerprint/extract_spikingresformer.py`
  — 切换为新的脉冲计数提取路径。
- `fingerprint/synth.py` — `make_synthetic_fingerprint` 当前直接生成
  `traffic_sequence`,本身就是一维数组,只需把 docstring/字段语义更新。
- `fingerprint/__init__.py` — 更新模块 docstring,标注新语义。
- `fingerprint/cli.py` — `_summary` 打印保持不变(仍消费 `traffic_sequence`)。
- `docs/fingerprint.md` §5.2 — 替换公式与代码索引。
- `tests/test_fingerprint_contract.py` — 当前断言 $E^{(t)}=\sum_{ij} W_{tij}$,
  需要新增"按脉冲计数聚合"的等价测试。
- `tests/test_fingerprint_pipeline.py`(若存在 SpikingJelly 集成测试)
  — 调整期望值。
- 可考虑停用的支撑模块(若新流程不再使用):`mask.py`、`slicing.py`、
  `edge_builder.py` — 第一阶段保留,待 §5.1 $\bar K$ 与 §5.3 $c^{*}_{\max}$
  的简化方案确定后再统一裁剪。

## 3. 数据契约(目标)

`Fingerprint.traffic_sequence: (T,) float32`,保持不变,语义改为:

$$
E^{(t)} \;=\; \frac{1}{N}\sum_{b=1}^{N}\sum_{i=1}^{V}
\#\{\text{neuron } n \in v_i : x^{(t)}_{i,\text{spike},b}[n] = 1\}
$$

其中 $N$ 是 val 集样本总数,$V$ 是模型中所有 LIF / spiking 节点的数量
(**不再做 §2.3 切分**)。CLI 通过 `--num-batches` 控制实际采样的样本
数 $N = B \times \text{num\_batches}$,只要 $N$ 取得够大,该均值就收敛
到"整个 val 集的逐样本均值"。

- 单位:`spikes / single inference`(原先是 `flits / single inference`,
  量纲变了 — Step B 互相关仍可工作,因为它只关心相对潮汐形状)。
- `compute_sequence` 可以保留为零(不再生成),或同步移除;留作 §5 决策。
- `traffic_sequence.shape == (T,)`,`traffic_sequence.dtype == float32`,
  退化:全零张量 ⇒ $\beta = 1$、$E^{(t)} \equiv 0$。

## 4. 实施步骤

按依赖顺序,每个 step 自成一个最小可验证单元:

### Step 1 — 在 `fingerprint/dtdg.py` 增加 spike-count 旁路

新增 `DTDGBuilder.spike_count_timeline_from_spikingjelly(net, dataloader, T, batches)`:

- 复用现有 `_is_spiking` 检测、forward-hook 注册逻辑。
- 每个 hook 对 `(T, B, ...)` 张量在 *非时间维 + 非样本维* 求 `sum(dim>=2)`,
  得到 `(T, B)` 的逐样本脉冲计数。
- **流式累加**(不要在 batch 内就先除以 $B$ 再求均值,否则当各 batch
  大小不等时与"val 全集均值"不等价):
  - 维护两个累加器 `sum_E: (T,) float64`、`n_samples: int`。
  - 每个 batch 跑完,把所有 LIF 节点的 `(T, B)` 张量拼起来对 `i` 维
    求和得到 `(T, B)`,再对 `B` 维 `sum` 加进 `sum_E`;同时
    `n_samples += B`。
  - 全部 batch 跑完返回 `(sum_E / n_samples).astype(float32)`,这等价
    于"先逐样本算 $E^{(t)}_b$,再做样本算术平均"。
- 不需要 slicing / mask / edge_builder。

接口签名:

```python
@staticmethod
def spike_count_timeline_from_spikingjelly(
    net,
    dataloader: Iterable,
    T: int,
    batches: int = 1,
) -> np.ndarray:  # shape (T,), dtype float32
    ...
```

### Step 2 — 给 `extract_fingerprint_from_W` 增加 spike-timeline 路径

提供 *两种入口*,共享后端中心性 / $\bar K$ 退化分支:

- 路径 A(legacy):仍接 `(T,V,V)` / `(T,V,V,2)` 张量,行为不变。
- 路径 B(新):提供 `extract_fingerprint_from_spikes(E, neuron_count, state_size_mb, ...)`
  接受一维 `E: (T,)`,直接计算 $\beta = \max E / \mathrm{mean}(E)$,
  $\bar K = 1.0$(单卡部署 ⇒ 全图视作单连通分量),
  $\mathbf{c}^{*}_{\max}$ 用均匀向量退化(后续可与 §5.1 / §5.3 简化方案
  再合并)。

`Fingerprint.compute_sequence` 与 `Fingerprint.centrality_var` 在路径 B
下返回零长度数组(或全零 `(T,)`,选其一)。

### Step 3 — 重接 `extract_spikformer.py` / `extract_spikingresformer.py`

把 `build_W_for_spikformer(...)` / `DTDGBuilder.from_spikingjelly(...)`
的调用替换为新的 `spike_count_timeline_from_spikingjelly(...)`。

CLI 参数 `--core-cap`、`--depths`、`--embed-dim`、`--num-heads` 在新路径
下不再使用(因为不再切分);保留但 deprecate,在 help 文本里加一行
`(deprecated under spike-count mode)`,或直接删除。

`neuron_count` 改为对所有 LIF 节点的物理 fan-out 求和(`sum_i |v_i|`),
不再依赖 `MicroPopulation`。

### Step 4 — 更新 `synth.py`

`make_synthetic_fingerprint(beta_target, T, ...)` 当前已直接生成 `(T,)`
`traffic_sequence`,语义与新方案兼容,仅需:

- docstring 把 "NoC flits expectation" 改成 "model-wide spike count
  expectation"。
- 默认 `neuron_count`、`state_size_mb` 不变。

### Step 5 — 重写 `docs/fingerprint.md` §5.2

- 替换 $E^{(t)}$ 的公式为新定义(脉冲计数求和)。
- 在 §5.2 增加一段 "为何可以简化":单模型 ⇒ 单卡 ⇒ 卡内 NoC 流量与
  脉冲发放总数同步,不需要 §4 掩码即可指导 Step B。
- 在 §3 / §4 / §6 顶部加 deprecation 注记:"以下边粒度建模为 v1 流程;
  v2 简化方案直接取脉冲计数,见 §5.2 注释块"。
- §7 schema 表里 `traffic_sequence` 的单位行改为 `spikes / single
  inference`。

### Step 6 — 测试

- `tests/test_fingerprint_contract.py`:
  - 保留对 `(T,V,V)` 路径的旧断言(回归)。
  - 新增对 `extract_fingerprint_from_spikes` 的断言:
    - shape / dtype 契约;
    - $\beta = \max E / \mathrm{mean}(E)$;
    - 全零输入 ⇒ $\beta=1$, $\bar K=0$ 或 $1$(待 §5.1 简化决策定夺,
      此 TODO 先在测试里写 `pytest.skip(reason=...)` 占位)。
- 若有 SpikingJelly 集成测试,改为调用新旁路,assert
  `fp.traffic_sequence.shape == (T,)` 且与 hook 手算的 spike count 一致。
- `tests/test_simulation_integration.py` 中
  `task.fingerprint.traffic_sequence.sum()` 的断言保持不变(语义仍是
  "一个 task 跑完 T tick 累积流量"),只是数量级会变小。

### Step 7 — 重新生成 `npz/`

旧的 `synthetic_*.npz` 用 `make_synthetic_fingerprint` 生成,无需重跑;
真实模型 npz(`spikformer_cifar10.npz`、`qkformer_cifar10.npz`、
`spikingresformer_ti_imagenet.npz`)需要用新 CLI 重新提取并落盘。
在 `npz/README.md` 注明 schema 版本号变更。

## 5. 与其它指纹的耦合(待后续 TODO 决策,不在本次范围)

- §5.1 $\bar K$:在单卡部署假设下,意义已变弱(任务一定整体落入同
  一张卡,Step A 的"碎片感知匹配"只剩下"哪张卡空间够大");可考虑
  退化为常数 1,或保留作为容量准入维度。**本 TODO 不修改**,
  `extract_fingerprint_from_spikes` 暂时硬编码 $\bar K = 1.0$。
- §5.3 $\mathbf{c}^{*}_{\max}$:同理,在单卡部署下意义降低;在新流程
  里用均匀向量 `1/V * ones(V)` 退化。**本 TODO 不修改语义**,只是不
  再计算。

## 6. 验证清单

- [ ] `pytest tests/` 全部通过(33+,允许补充新用例)。
- [ ] `python -m fingerprint.cli --synthetic --T 64 --beta 4 --K 2
  --out /tmp/syn.npz` 仍能跑通,`fp.traffic_sequence.shape == (64,)`。
- [ ] `python -m fingerprint.extract_spikformer --dataset cifar10 --T 4
  --out /tmp/fp.npz` 端到端跑通,`fp.traffic_sequence` 数值等于
  hook 手算的 spike count 期望(误差 $< 10^{-4}$)。
- [ ] `make stps CARDS=2 TASKS=4 STEPS=4 ARRIVAL_MODE=poisson SEED=1`
  smoke 测试无回归。
- [ ] `docs/fingerprint.md` §5.2 与本方案一致。

## 7. 不做的事

- 不删除 `slicing.py` / `mask.py` / `edge_builder.py`:留作 v1 流程
  入口,以备审稿需要边粒度精确建模时回滚。
- 不改 `Fingerprint` dataclass 字段名/形状 — 只改写入 `traffic_sequence`
  的语义。
- 不动 `simulation/`、`schedule/` 任何代码 — Step B 互相关只读
  `fp.traffic_sequence`,无需感知语义变化。
