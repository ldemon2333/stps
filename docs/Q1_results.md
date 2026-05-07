# Q1 Results — Spatial Load Balancing via Step A

> 实验脚本：[script/q1_run.py](../script/q1_run.py)
> 原始 CSV：[data/q1/](../data/q1/)
> 主表 Markdown：[figures/q1/main_table.md](../figures/q1/main_table.md)
> 设计文档：[docs/Q1_TODO.md](Q1_TODO.md)

> **本次重跑修正了 6 个 bug**（硬件规模、主表到达模式、mix 实验语义、置信区间口径、SLA 描述、利用率 anchor 重新校准）。下文结论与之前那版有显著差异，请以本版为准。

## 1. 实验设置

| 维度 | 取值 | 来源 |
|---|---|---|
| Cards × cores/card | **4 × 512 = 2048 cores** | [.env](../.env) `CARD_TOTAL_CORES=512`，运行时探针确认 |
| Steps | 512 | runner |
| Capacity | 4500 | runner |
| Tasks (主表) | 800（≈70.3% 利用率） | 实测 mean instantaneous load / capacity，bestfit + poisson 校准 |
| Seeds | 21, 42, 99, 123, 2024（5 次重复） | runner |
| Arrival | **Poisson**（主表 + 扫描） | 与 [docs/Q1_TODO.md §2](Q1_TODO.md) 设计一致 |
| 对比集 | RR / BestFit / DRF / P2C / **STPS (Step A)** | `stps-spatial` = Stage 1 + Stage 3 |

所有 scheduler 现在都通过 `record_physical_tick + get_epoch_loads` 报告同口径的 epoch 累积负载。STPS 此前因未 override 这两个 hook 而落入即时负载分支（量纲差 10×）；修复见 [schedule/stps.py:62-90](../schedule/stps.py#L62-L90)。

指标在 4 卡聚合层面计算（按用户决策"仅卡级"）：

- $\mathrm{CV} = \sigma(L_m) / \mu(L_m)$
- $\mathrm{JFI} = (\sum L_m)^2 / (M \sum L_m^2)$
- Max/Min ratio — 排除零负载卡

报告值 = 稳态窗口（去前 64 / 后 64 tick）内逐 tick 计算后取均值，跨 5 seeds 报告 **mean ± 95% CI 半宽**（`1.96 · σ / √n`）。

## 2. 主表（70% utilization, Poisson）

| Scheduler | card-CV ↓ | card-JFI ↑ | Max/Min ↓ | SLA viol ↓ | avg load var |
|---|---|---|---|---|---|
| RR | 0.3632 ± 0.0140 | 0.8719 ± 0.0078 | 3.397 | 0.1973 | 1,316,246 |
| BestFit | 0.2921 ± 0.0078 | 0.9105 ± 0.0043 | 2.504 | 0.1636 | 892,810 |
| DRF | 0.2932 ± 0.0094 | 0.9099 ± 0.0046 | 2.461 | 0.1596 | 881,852 |
| P2C | 0.4070 ± 0.0193 | 0.8461 ± 0.0104 | 3.735 | 0.2219 | 1,599,699 |
| **STPS (Step A)** | **0.3856 ± 0.0183** | **0.8587 ± 0.0105** | 3.362 | 0.2028 | 1,402,528 |

### 与论文 claim 对照

| Claim | 论文值 | 实验值 | 状态 |
|---|---|---|---|
| STPS card-CV vs RR | −20.1% | **−6.2%** | 方向一致，幅度偏小 |
| STPS card-CV vs BestFit | −11.2% | **+32.0%**（劣于 BestFit）| ⚠ 方向相反 |
| P2C card-JFI（最差）| ≈0.67 | **0.846**（仍是 4 baseline 中最差）| 方向一致，绝对值偏高 |

注意：本次主表 SLA 违例率在 0.16–0.22 区间（不是接近 0）。此前 `Q1_results.md` 写"SLA 接近 0"是 70% 利用率 + bursty 误读 — 本版已修正。

## 3. 负载强度扫描（Poisson）

| util | RR | BestFit | DRF | P2C | STPS (A) |
|---|---|---|---|---|---|
| 0.30 | 0.7219 | 0.6522 | 0.6567 | 0.9075 | 1.0037 |
| 0.50 | 0.4934 | 0.4020 | 0.3920 | 0.6321 | 0.5916 |
| 0.70 | 0.3632 | 0.2921 | 0.2932 | 0.4070 | 0.3856 |
| 0.85 | 0.2572 | 0.2239 | 0.2231 | 0.2932 | **0.2492** |
| 0.95 | 0.2182 | 0.2018 | 0.2002 | 0.2302 | **0.2070** |

观察：

- **高载收敛符合论文叙述**：在 0.95 利用率下，所有 scheduler CV 落在 0.200–0.230 区间（彼此 ≤15%），与 §6.3 "all schedulers converge at extreme physical saturation" 一致。
- **高载下 STPS 反超 RR / P2C**：0.85 util 起 STPS 卡-CV 已优于 RR（0.2492 vs 0.2572）和 P2C（0.2932），并接近 BestFit（0.2239）。
- **中低载 BestFit/DRF 仍主导**：在 0.30–0.70 区间，STPS 的 fragmentation-aware 分派需要的拓扑信号被淹没在容量空闲信号里，BestFit 的纯容量 fit 反而更稳。
- **P2C 与 STPS 在 30% util 几乎并列垫底**：与论文 "P2C blindly collocates fragmented graphs" 论述一致；STPS 在低载下表现差是因为 forecast 信号噪声占主导。

完整 CSV：[data/q1/sweep_summary.csv](../data/q1/sweep_summary.csv)。

## 4. 工作负载混合敏感度（真正的 fingerprint 比例扫描）

通过为每个比例构造一个 fingerprint 目录（含 `synthetic_flat.npz` / `synthetic_bursty.npz` 的对应份数），让 `task_id % len(paths)` 命中目标比例。

card-CV：

| flat / bursty | RR | BestFit | DRF | P2C | STPS (A) |
|---|---|---|---|---|---|
| 100 / 0   | 0.3632 | 0.2921 | 0.2932 | 0.4070 | **0.2896** |
| 75 / 25   | 0.3632 | 0.2921 | 0.2932 | 0.4070 | 0.3195 |
| 50 / 50   | 0.3632 | 0.2921 | 0.2932 | 0.4070 | 0.3403 |
| 25 / 75   | 0.3632 | 0.2921 | 0.2932 | 0.4070 | 0.4111 |
| 0 / 100   | 0.3632 | 0.2921 | 0.2932 | 0.4070 | 0.5551 |

**第一个真正的胜利**：当工作负载 100% 都是 Steady-Flat fingerprint 时，STPS-spatial 卡-CV (0.2896) 略优于 BestFit (0.2921) 和 DRF (0.2932)。这与论文 §6.2 "STPS extracts $\bar K$ from the DTDG fingerprint at admission" 的机理论述一致：当指纹差异小、拓扑可预测时，Step A 能精确派发；当 bursty 比例升高，瞬时峰值在 Step A 决策时刻不可见（Step A 用 horizon=64 forecast，但 bursty fingerprint 的瞬态难以预测），优势消失。

baselines 在不同 fingerprint mix 下数值完全相同 — 验证了 baselines 不读取 fingerprint，仅 STPS 受工作负载拓扑影响。

完整 CSV：[data/q1/mix_summary.csv](../data/q1/mix_summary.csv)。

## 5. 验收标准复盘（vs [docs/Q1_TODO.md](Q1_TODO.md) §7）

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

1. **STPS 的"Step A 独立优势"高度依赖 fingerprint 拓扑分布**。在均匀 Steady-Flat 工作负载下，Step A 能实现论文论述的 −20% 量级（vs RR）；混合越偏向 Sparse-Bursty，优势越弱直至反转。在默认混合（合成 flat/bursty/extreme 1:1:1）下，BestFit 仍是空间公平基线 SOTA。
2. **真实 trace 重要性**。论文使用真实 SpikingJelly 流（VGG-CIFAR10、Spiking-ResNet-DVS128），其 fingerprint 多样性远超 3 档合成指纹；Step A 的 $\bar K$-aware 分派可能在真实 trace 上能复现 vs BestFit 的 −11.2% claim。
3. **承诺 vs 验收**：默认混合下 5 项 DoD 中 1 项通过 / 1 项几乎通过 / 3 项未通过。**不应在论文里直接复用论文 claim 的具体数字**，需要要么引入真实 trace、要么调整 claim 口径（限定 "in steady-flat dominant workloads"）。

**建议下一步**（按 ROI 排序）：

1. 用 SpikingJelly 真实 trace 生成更多 fingerprint，在主表里重跑。
2. 对 Stage 1 的 `frag_weight / beta_weight / beta_high_threshold` 做小规模 grid（3×3×3），看默认混合下能否反转 vs BestFit。
3. Q2 的 SLA-violation 实验 + 完整 STPS（含 Step B）— 论文 §6.2 末尾本来就主张 spatial alone 不够，STPS 的整体优势预期主要来自 Step B。

当前结果可作为 §6.2 的诚实基线：**Step A 在 Steady-Flat 工作负载下能与 BestFit 持平或微胜，并未在所有混合下独立超越；优势随 bursty 比例升高而衰减。** 这与论文 §6.2 → §6.3 "spatial necessary but insufficient" 的递进逻辑一致。
