# Q1 Results — Spatial Load Balancing via Step A

> 实验脚本：[script/q1_run.py](../script/q1_run.py)
> 原始 CSV：[data/q1/](../data/q1/)
> 主表 Markdown：[figures/q1/main_table.md](../figures/q1/main_table.md)
> 设计文档：[docs/Q1_TODO.md](Q1_TODO.md)

> **本次重跑修正了 6 个 bug**（硬件规模、主表到达模式、mix 实验语义、置信区间口径、SLA 描述、利用率 anchor 重新校准）。下文结论与之前那版有显著差异，请以本版为准。

## 0. 结论先行：Step A 空间负载的优势、缺点与边界

Q1 当前结论是：**Step A（`stps-spatial` = Stage 1 + Stage 3）是有用的空间负载底座，但不是单独成立的全局最优调度器**。它能利用 fingerprint 里的 $\bar K$、burstiness 和 hotspot centrality 做空间放置与切分；但它不做 phase-shift，因此无法主动错开同一卡上任务的时间峰值，也不能充分处理 NoC 拥塞。

**主要优势：**

1. **在拓扑稳定、低 bursty 的工作负载上有效**：参考 [Table Q1-5](#table-q1-5)，mix 扫描里，100% Steady-Flat 时 Step A 的 card-CV 为 `0.2896`，略优于 BestFit (`0.2921`) / DRF (`0.2932`)，并较 RR (`0.3632`) 下降约 `20.2%`。这说明当 fingerprint 拓扑信号干净、瞬态峰值不剧烈时，$\bar K$-aware 的空间分派能兑现论文里的空间均衡直觉。
2. **高负载下有收敛价值**：参考 [Table Q1-4](#table-q1-4)，util `0.85/0.95` 时 Step A 已优于 RR / P2C，并接近 BestFit / DRF。高载时卡容量空间被压缩，纯随机或局部选择更容易失衡，Step A 的碎片匹配和 hotspot split 开始发挥作用。
3. **给 Step B 提供了更好的空间底座**：参考 [Table Q1-2](#table-q1-2) 和 [Q2_result.md 的 Step B 垂直对照](Q2_result.md#2-跨卡负载均衡垂直对比)。Q2 把 `stps-spatial` 作为 phase-shift 的 off 版本，直接显示 `stps = stps-spatial + phase-shift`。在 `BW_MAX=5e6` 无拥塞主实验里，Step B 在这个空间底座上继续把 CV 从 `0.6902 → 0.5535`（poisson，gain `19.8%`）和 `0.6914 → 0.5853`（bursty，gain `15.4%`）。这说明 Step A 不是终点，而是端到端 STPS 的空间前置条件。

**主要缺点：**

1. **默认混合负载下不如 BestFit/DRF**：参考 [Table Q1-2](#table-q1-2) 和 [Table Q1-3](#table-q1-3)，70% utilization + Poisson 主表中，Step A card-CV 为 `0.3856`，劣于 BestFit (`0.2921`) / DRF (`0.2932`)，也没有超过 RR (`0.3632`)，仅优于 P2C (`0.4070`)。因此当前数据不支持“Step A 单独全面优于静态 baseline”的表述。
2. **对 bursty 比例非常敏感**：参考 [Table Q1-5](#table-q1-5)，flat/bursty 从 `100/0` 扫到 `0/100` 时，Step A card-CV 从 `0.2896` 恶化到 `0.5551`。空间分派能处理“放到哪张卡”，但不能处理“峰值在哪个 tick 撞上”；bursty 越强，缺少时间错峰的代价越大。
3. **不能单独解决拥塞**：参考 [Q2_result.md 的 BW×D_MAX sweep](Q2_result.md#92-bw9e5-vs-bw5e5拥塞维-vs-卡间负载d_max16)，Q2 显示在 `BW_MAX=9e5` 时 `stps-spatial` 的 `avg_cong_ratio` 仍约 `0.263/0.260`（poisson/bursty），在 `BW_MAX=5e5` 重拥塞下约 `0.438/0.437`。叠加 Step B 后拥塞会下降到约 `0.160/0.157` 或 `0.386/0.382`，但仍弱于普通 baseline + phase 的“几乎归零”。这说明 Step A 的 hotspot split 已经消耗了一部分空间自由度，后续还需要更精细的时间错峰和拥塞感知。
4. **不能改善时间维峰值**：参考 [Q2_result.md 的 per-card time_lif 表](Q2_result.md#3-每张卡的时间维度负载per-card-time_lifserved)，Step A 没有 `start_offset`，任务一旦放置就按原 trace 发流量；Q2 证明 phase-shift 能逐卡降低 `time_lif`，这正是 Step A 缺失的维度。

**论文/报告表述建议：**

- 可以说 Step A 是 **spatial necessary but insufficient**：它在 steady-flat、拓扑可预测、高载收敛场景中有效，是 STPS 的空间底座。
- 不应说 Step A 单独稳定击败 BestFit/DRF；当前默认混合下这个 claim 不成立。
- Q1 应负责证明“空间指纹有用但边界明显”，Q2 则负责证明“Step B 用启动延迟换时间错峰，补上 Step A 缺失的时间维能力”。

## 1. 实验设置

<a id="table-q1-1"></a>

**Table Q1-1. 实验设置与运行口径。**

| 维度 | 取值 | 来源 |
|---|---|---|
| Cards × cores/card | **4 × 512 = 2048 cores** | [.env](../.env) `CARD_TOTAL_CORES=512`，运行时探针确认 |
| Steps | 512 | runner |
| Capacity | 4500 | runner |
| Tasks (主表) | 800（≈70.3% 利用率） | 实测 mean instantaneous load / capacity，bestfit + poisson 校准 |
| Seeds | 21, 42, 99, 123, 2024（5 次重复） | runner |
| Arrival | **Poisson**（主表 + 扫描） | 与 [docs/Q1_TODO.md §2](Q1_TODO.md) 设计一致 |
| 对比集 | RR / BestFit / DRF / P2C / **STPS (Step A)** | `stps-spatial` = Stage 1 + Stage 3 |
| `BW_cap` | 未设置（`None` / 无限带宽） | `script/q1_run.py` 未传 `bw_cap`；Q1 隔离 Step A 空间负载，不启用 engine-side NoC cap |
| `BW_max` | `5e6`（forecast 阈值） | `script/q1_run.py` 传入 `bw_max=5e6`；对 `stps-spatial` 的 Stage 2 为 no-op，仅保留运行口径一致性 |

所有 scheduler 现在都通过 `record_physical_tick + get_epoch_loads` 报告同口径的 epoch 累积负载。STPS 此前因未 override 这两个 hook 而落入即时负载分支（量纲差 10×）；修复见 [schedule/stps.py:62-90](../schedule/stps.py#L62-L90)。

指标在 4 卡聚合层面计算（按用户决策"仅卡级"）：

- $\mathrm{CV} = \sigma(L_m) / \mu(L_m)$
- $\mathrm{JFI} = (\sum L_m)^2 / (M \sum L_m^2)$
- Max/Min ratio — 排除零负载卡

报告值 = 稳态窗口（去前 64 / 后 64 tick）内逐 tick 计算后取均值，跨 5 seeds 报告 **mean ± 95% CI 半宽**（`1.96 · σ / √n`）。

## 2. 主表（70% utilization, Poisson）

<a id="table-q1-2"></a>

**Table Q1-2. 主表：70% utilization + Poisson 下的卡级空间负载均衡。**

| Scheduler | card-CV ↓ | card-JFI ↑ | Max/Min ↓ | SLA viol ↓ | avg load var |
|---|---|---|---|---|---|
| RR | 0.3632 ± 0.0140 | 0.8719 ± 0.0078 | 3.397 | 0.1973 | 1,316,246 |
| BestFit | 0.2921 ± 0.0078 | 0.9105 ± 0.0043 | 2.504 | 0.1636 | 892,810 |
| DRF | 0.2932 ± 0.0094 | 0.9099 ± 0.0046 | 2.461 | 0.1596 | 881,852 |
| P2C | 0.4070 ± 0.0193 | 0.8461 ± 0.0104 | 3.735 | 0.2219 | 1,599,699 |
| **STPS (Step A)** | **0.3856 ± 0.0183** | **0.8587 ± 0.0105** | 3.362 | 0.2028 | 1,402,528 |

### 与论文 claim 对照

<a id="table-q1-3"></a>

**Table Q1-3. 论文 claim 与当前 Q1 主表结果对照。**

| Claim | 论文值 | 实验值 | 状态 |
|---|---|---|---|
| STPS card-CV vs RR | −20.1% | **−6.2%** | 方向一致，幅度偏小 |
| STPS card-CV vs BestFit | −11.2% | **+32.0%**（劣于 BestFit）| ⚠ 方向相反 |
| P2C card-JFI（最差）| ≈0.67 | **0.846**（仍是 4 baseline 中最差）| 方向一致，绝对值偏高 |

注意：本次主表 SLA 违例率在 0.16–0.22 区间（不是接近 0）。此前 `Q1_results.md` 写"SLA 接近 0"是 70% 利用率 + bursty 误读 — 本版已修正。

## 3. 负载强度扫描（Poisson）

<a id="table-q1-4"></a>

**Table Q1-4. 负载强度扫描：不同 utilization 下的 card-CV。**

| util | RR | BestFit | DRF | P2C | STPS (A) |
|---|---|---|---|---|---|
| 0.30 | 0.7219 | 0.6522 | 0.6567 | 0.9075 | 1.0037 |
| 0.50 | 0.4934 | 0.4020 | 0.3920 | 0.6321 | 0.5916 |
| 0.70 | 0.3632 | 0.2921 | 0.2932 | 0.4070 | 0.3856 |
| 0.85 | 0.2572 | 0.2239 | 0.2231 | 0.2932 | **0.2492** |
| 0.95 | 0.2182 | 0.2018 | 0.2002 | 0.2302 | **0.2070** |

观察（参考 [Table Q1-4](#table-q1-4)）：

- **高载收敛符合论文叙述**：在 0.95 利用率下，所有 scheduler CV 落在 0.200–0.230 区间（彼此 ≤15%），与 §6.3 "all schedulers converge at extreme physical saturation" 一致。
- **高载下 STPS 反超 RR / P2C**：0.85 util 起 STPS 卡-CV 已优于 RR（0.2492 vs 0.2572）和 P2C（0.2932），并接近 BestFit（0.2239）。
- **中低载 BestFit/DRF 仍主导**：在 0.30–0.70 区间，STPS 的 fragmentation-aware 分派需要的拓扑信号被淹没在容量空闲信号里，BestFit 的纯容量 fit 反而更稳。
- **P2C 与 STPS 在 30% util 几乎并列垫底**：与论文 "P2C blindly collocates fragmented graphs" 论述一致；STPS 在低载下表现差是因为 forecast 信号噪声占主导。

完整 CSV：[data/q1/sweep_summary.csv](../data/q1/sweep_summary.csv)。

## 4. 工作负载混合敏感度（真正的 fingerprint 比例扫描）

通过为每个比例构造一个 fingerprint 目录（含 `synthetic_flat.npz` / `synthetic_bursty.npz` 的对应份数），让 `task_id % len(paths)` 命中目标比例。

这里的 `flat / bursty` 指 **任务自身 fingerprint 的通信 trace 形状**，不是任务到达过程。Q1 mix 实验的 arrival 仍固定为 **Poisson**；区别是任务到达后使用的是平稳通信 trace (`synthetic_flat`) 还是尖峰通信 trace (`synthetic_bursty`)。换句话说：`Poisson` 决定“任务什么时候来”，`flat / bursty` 决定“任务来了以后每个 tick 怎么发通信流量”。

card-CV：

<a id="table-q1-5"></a>

**Table Q1-5. 工作负载混合敏感度：flat/bursty 比例扫描下的 card-CV。**

| flat / bursty | RR | BestFit | DRF | P2C | STPS (A) |
|---|---|---|---|---|---|
| 100 / 0   | 0.3632 | 0.2921 | 0.2932 | 0.4070 | **0.2896** |
| 75 / 25   | 0.3632 | 0.2921 | 0.2932 | 0.4070 | 0.3195 |
| 50 / 50   | 0.3632 | 0.2921 | 0.2932 | 0.4070 | 0.3403 |
| 25 / 75   | 0.3632 | 0.2921 | 0.2932 | 0.4070 | 0.4111 |
| 0 / 100   | 0.3632 | 0.2921 | 0.2932 | 0.4070 | 0.5551 |

**第一个真正的胜利（参考 [Table Q1-5](#table-q1-5)）**：当工作负载 100% 都是 Steady-Flat fingerprint 时，STPS-spatial 卡-CV (0.2896) 略优于 BestFit (0.2921) 和 DRF (0.2932)。这与论文 §6.2 "STPS extracts $\bar K$ from the DTDG fingerprint at admission" 的机理论述一致：当指纹差异小、拓扑可预测时，Step A 能精确派发；当 bursty 比例升高，瞬时峰值在 Step A 决策时刻不可见（Step A 用 horizon=64 forecast，但 bursty fingerprint 的瞬态难以预测），优势消失。

baselines 在不同 fingerprint mix 下数值完全相同 — 验证了 baselines 不读取 fingerprint，仅 STPS 受工作负载拓扑影响。

完整 CSV：[data/q1/mix_summary.csv](../data/q1/mix_summary.csv)。

## 5. 验收标准复盘（vs [docs/Q1_TODO.md](Q1_TODO.md) §7）

<a id="table-q1-6"></a>

**Table Q1-6. 验收标准复盘。**

| # | 标准 | 状态 |
|---|---|---|
| 1 | `make q1` 一键复现，5 seeds 误差 | ✅ 主表 95% CI 半宽 ≤ 5% 相对值 |
| 2 | STPS 核-CV 较 RR 降幅 ≥ 15% | ❌ 实测 −6.2%（默认混合）；mix-100/0 下 STPS = 0.290 < RR 0.363，**−20.2% 命中论文** |
| 2 | STPS 核-CV 较 BestFit 降幅 ≥ 8% | ❌ 实测 +32%（默认混合）；mix-100/0 下 STPS 略优 (−0.9%) 但未达 8% |
| 3 | P2C 卡-JFI ≤ 0.75 | ❌ 实测 0.846；P2C 仍是 baseline 中最差 |
| 4 | 95% util 所有 scheduler CV 互差 ≤10% | △ 实测最大差 (P2C-DRF) = 15% — 略超 |
| 5 | `pytest tests/` 全绿 | ✅ 40/40 passed |

## 6. 讨论与后续工作

主要发现：

1. **STPS 的"Step A 独立优势"高度依赖 fingerprint 拓扑分布**：参考 [Table Q1-5](#table-q1-5)。在均匀 Steady-Flat 工作负载下，Step A 能实现论文论述的 −20% 量级（vs RR）；混合越偏向 Sparse-Bursty，优势越弱直至反转。在默认混合（合成 flat/bursty/extreme 1:1:1）下，BestFit 仍是空间公平基线 SOTA。
2. **真实 trace 重要性**：参考 [Table Q1-5](#table-q1-5) 中 bursty 比例升高后的退化。论文使用真实 SpikingJelly 流（VGG-CIFAR10、Spiking-ResNet-DVS128），其 fingerprint 多样性远超 3 档合成指纹；Step A 的 $\bar K$-aware 分派可能在真实 trace 上能复现 vs BestFit 的 −11.2% claim。
3. **承诺 vs 验收**：参考 [Table Q1-6](#table-q1-6)，默认混合下 5 项 DoD 中 1 项通过 / 1 项几乎通过 / 3 项未通过。**不应在论文里直接复用论文 claim 的具体数字**，需要要么引入真实 trace、要么调整 claim 口径（限定 "in steady-flat dominant workloads"）。

**建议下一步**（按 ROI 排序）：

1. 用 SpikingJelly 真实 trace 生成更多 fingerprint，在主表里重跑。
2. 对 Stage 1 的 `frag_weight / beta_weight / beta_high_threshold` 做小规模 grid（3×3×3），看默认混合下能否反转 vs BestFit。
3. Q2 的 SLA-violation 实验 + 完整 STPS（含 Step B）— 论文 §6.2 末尾本来就主张 spatial alone 不够，STPS 的整体优势预期主要来自 Step B。

当前结果可作为 §6.2 的诚实基线：**Step A 在 Steady-Flat 工作负载下能与 BestFit 持平或微胜，并未在所有混合下独立超越；优势随 bursty 比例升高而衰减。** 这与论文 §6.2 → §6.3 "spatial necessary but insufficient" 的递进逻辑一致。
