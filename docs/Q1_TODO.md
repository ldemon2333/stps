# Q1 实验设计：Spatial Load Balancing via Step A

> 对应 [article.tex](../article.tex) §6.2（`\subsection{Q1: Spatial Load Balancing via Step A}`）。
> 目标：验证 STPS 的 **Step A（Macro-Card Dispatching）** 在 $M=4$ 卡 / 1024 核集群上比 RR / BestFit / DRF / P2C 显著降低空间碎片化。

## 1. 实验目标

回答论文中的两个 claim：

1. **Mitigating Head-of-Line Fragmentation** — STPS 的 CV 比 RR 低 ~20%、比 BestFit 低 ~11%。机理：Step A 通过 DTDG 指纹中的 $\bar{K}$（active connected components）把高内聚子图整体投到有连续空闲 PIM 块的卡上。
2. **Fallacy of Randomized Placement** — P2C 的 JFI 最差（≈0.67）。机理：随机派发对结构异质的 SNN 任务无效。

二者都是 **纯空间** 命题，因此实验必须**隔离 Step B（temporal phase-shifting）的影响**。

## 2. 控制变量原则

| 维度 | 设定 | 备注 |
|------|------|------|
| Step A | **变量**：rr / bestfit / drf / p2c / stps | 唯一被对比的算法层 |
| Step B | **统一关闭**（或对所有 scheduler 都关闭） | 论文原文："All baselines adopt the same Step C, while Step B represents a feature specific to SNN tasks." Q1 不评测 B，统一 disable |
| Step C | **统一启用**默认 micro-mapping（含 hotspot split） | 所有 scheduler 共用，确保碎片化差异完全来自 Step A |
| 工作负载 | Poisson 到达，混合 Steady-Flat + Sparse-Bursty 指纹 | 论文 §6.1：连续 Poisson 流 |
| 硬件 | $M=4$ 卡 × 256 核 = 1024 核，$24\times24$ Mesh | 与 §6.1 Hardware Setup 一致 |

**实现要点**：在 `STPSScheduler` 中加 `--disable-phase-shift` 开关（或复用现有 `stps-spatial` 消融，它已经是 Stage 1 + Stage 3，无 phase shift）；baselines 本身就没有 Step B。这样 Q1 的对比集就是 `{rr, bestfit, drf, p2c, stps-spatial}` 在概念上等价于 "Step A 五选一 + 同一 Step C"。

> ⚠️ **Decision point**：Q1 的 "STPS" 列用 `stps-spatial`，与论文 "Step A only" 的论述完全对齐；最终表格列名仍写 "STPS (Step A)"。

## 3. 评测指标

均在 1024 个核（$4\times256$）维度上、按时间步逐 tick 计算后取平均：

| 指标 | 公式 | 期望方向 |
|------|------|----------|
| **核级 CV** | $\mathrm{CV}_t = \sigma(L_{c,t}) / \mu(L_{c,t})$，$L_{c,t}$ 为核 $c$ 在 tick $t$ 的负载 | ↓ |
| **核级 JFI** | $\mathrm{JFI}_t = (\sum_c L_{c,t})^2 / (N \sum_c L_{c,t}^2)$，$N=1024$ | ↑（→1） |
| **卡级 CV** | 同上但聚合到 $M=4$ 张卡 | ↓ |
| **卡级 JFI** | 同上 | ↑ |
| **Max/Min core load ratio** | $\max_c L_{c,t} / \min_c L_{c,t}$（剔除空闲核） | ↓ |
| **Active core fraction** | 非零负载核占比 | 用于解释碎片化形态 |

报告值 = **稳态窗口** 内（去掉前 64 / 后 64 tick 的预热和尾段）所有 tick 的算术平均 ± 95% CI（across seeds）。

> 📌 现有 [util/metrics.py](../util/metrics.py) 已有卡级 `load_variance`，但没有 CV / JFI 也没有核级粒度。**需扩展**：
>
> - 在 `LoadSnapshot` 上加 `cv`、`jfi` 属性（卡级，零开销）。
> - 新增**核级 snapshot**：每 tick 从每张卡的 `Card` 取出核占用向量（长度 256），拼成 1024-维向量再算 CV/JFI。需要在 [util/card.py](../util/card.py) 暴露核占用数组（多数 placement 已经在内部维护，确认即可）。
> - `RunMetrics` 新增 `avg_core_cv / avg_core_jfi / avg_card_cv / avg_card_jfi`。

## 4. 实验矩阵

### 4.1 主表（Table `tab:poisson_perf` 占位）

固定 $M=4, \text{cores}=1024, \text{steps}=512, \lambda$ 使稳态利用率 ≈ 70%，单 scheduler 跑 5 seeds 取均值。

| Scheduler | 核-CV ↓ | 核-JFI ↑ | 卡-CV ↓ | 卡-JFI ↑ | Max/Min ↓ |
|-----------|---------|----------|---------|----------|-----------|
| RR        |         |          |         |          |           |
| BestFit   |         |          |         |          |           |
| DRF       |         |          |         |          |           |
| P2C       |         |          |         |          |           |
| **STPS (Step A)** |   |    |         |          |           |

主结论需达成：STPS 核-CV 较 RR 降 ≈20%、较 BestFit 降 ≈11%；P2C 的 JFI 落到 ≈0.67。

### 4.2 负载强度扫描（折线图）

$\lambda$ 扫 `{30%, 50%, 70%, 85%, 95%}` 利用率，画 5 条曲线（每 scheduler 一条），y 轴核-CV。预期：

- 低载（≤50%）所有方法都接近 0；
- 中载（70–85%）STPS 显著领先；
- 高载（95%）所有方法收敛（与 §6.3 论述一致）。

### 4.3 工作负载混合敏感度

Steady-Flat / Sparse-Bursty 任务比例扫 `{100/0, 75/25, 50/50, 25/75, 0/100}`。期望：bursty 比例越高，STPS 相对 BestFit 的优势越大（因为 BestFit 把 bursty 任务的瞬时峰值堆到同一张卡）。

### 4.4 鲁棒性

- 多 seed：`SEED ∈ {21, 42, 99, 123, 2024}`，主表的误差棒来源。
- $\lambda$ 抖动：Poisson vs. mixed 到达，验证 STPS 的优势不依赖到达过程。

## 5. 需要的代码改动

> 控制范围：**只做 Q1 必需的改动**，不引入与 Q2/Q3 无关的抽象。

1. **核级负载暴露**：[util/card.py](../util/card.py) 增加 `core_loads -> np.ndarray[256]` property（如已存在则跳过）。
2. **指标扩展**：[util/metrics.py](../util/metrics.py)
   - `LoadSnapshot.cv / jfi`（卡级）
   - 新 `CoreLoadSnapshot` 或在现有 snapshot 中嵌入核级向量
   - `RunMetrics.{avg_core_cv, avg_core_jfi, avg_card_cv, avg_card_jfi, max_min_ratio}`
   - `to_summary_dict` 输出新增字段
3. **Engine hook**：[simulation/engine.py](../simulation/engine.py) 在每个 tick 末尾调用核级 snapshot 写入。性能：1024 维 × 512 tick × 5 seeds × 5 schedulers ≈ 13M 数据点，可接受。
4. **CSV 列**：`data/{scheduler}_summary_*.csv` 新增 `core_cv, core_jfi, card_cv, card_jfi, max_min_ratio` 列。
5. **绘图**：在 `plot/`（已被删除，需新建）下加：
   - `plot/q1_table.py` — 主表 CSV → markdown / LaTeX
   - `plot/q1_load_sweep.py` — §4.2 折线
   - `plot/q1_workload_mix.py` — §4.3 grouped bar
6. **Make 目标**：`Makefile` 新增
   ```make
   q1:           ## Q1 主表（5 schedulers × 5 seeds）
   q1-sweep:     ## Q1 负载扫描
   q1-mix:       ## Q1 工作负载混合
   q1-all: q1 q1-sweep q1-mix
   ```
7. **脚本**：`script/q1_main.sh` 串联三个目标并产出最终图表/表格到 `figures/q1/`。

## 6. 产出物清单

- `data/q1/*.csv` — 原始指标
- `figures/q1/main_table.tex`（或 `.md`）— 论文 Table 6.2 主表
- `figures/q1/load_sweep.pdf` — 负载扫描折线
- `figures/q1/workload_mix.pdf` — 工作负载混合柱状
- `docs/Q1_results.md` — 数值与论文 claim 的对照清单（CV ↓20%、JFI ≈0.67 等）

## 7. 验收标准（Definition of Done）

1. 主表 5 行全部跑通，`make q1` 一键复现，5 seeds 误差棒 < 5% 相对值。
2. STPS 核-CV 相对 RR 降幅 ≥ 15%（论文 20.1%，留 5% 容差）；相对 BestFit ≥ 8%（论文 11.2%）。
3. P2C 卡-JFI ≤ 0.75（论文 0.67）。
4. 负载扫描曲线在 95% 利用率处，所有 scheduler 的 CV 收敛到彼此 10% 以内。
5. `pytest tests/` 全绿（新指标必须有单元测试，至少覆盖 CV、JFI、max/min 三个公式）。

## 8. 时间线（建议）

| 阶段 | 内容 | 预估 |
|------|------|------|
| 1 | 指标扩展 + 单元测试 | 0.5 天 |
| 2 | Engine 集成 + CSV 列 | 0.5 天 |
| 3 | 主表运行 + 调试 | 0.5 天 |
| 4 | 负载扫描 + 工作负载混合 | 1 天 |
| 5 | 绘图 + 论文表生成 + `Q1_results.md` | 0.5 天 |

## 9. 负载来源切换：从 `simulate_tick` 合成到 `Fingerprint.E^(t)` ✅

> **现状**：每个 task 的每物理 tick 负载直接取自其指纹的 `E[(active_tick - 1) mod T]`，由 [util/task.py:39-44](../util/task.py#L39-L44) `Task.simulate_tick` 实现；卡级 epoch 累计在所有 scheduler（baselines + STPS）里都改为 `Σ task.current_traffic`，量纲与 Step B 的 forecast / `add_forecast` 完全一致。
>
> 旧合成路径（hotspot gating + sinusoid + 高斯噪声 + α/β 加权）已彻底移除，无回退、无双轨。

### 9.1 已落地的清单

1. [util/task.py](../util/task.py) `Task`：
   - 删除 `current_spike_count` / `current_synaptic_ops` / `firing_rate_history` / `fan_out` / `avg_hop_distance` / hotspot 参数。
   - 新增 `current_traffic: float`；`simulate_tick()` 直接读 `fingerprint.E[(tick_index-1) mod T]`，无指纹会触发 `AttributeError`（设计上 baseline & STPS 都必须有指纹）。
   - 相位移由 engine 门控触发：offset 期间 engine 把 `current_traffic` 显式置 0，`tick_index` 不增长。
2. [util/card.py](../util/card.py)：`calculate_load()` 改为 `sum(task.current_traffic)`，删除 `alpha/beta` 入参；删除 `calculate_comm_load` / `calculate_composite_load`（无消费方）。
3. [schedule/base.py](../schedule/base.py)：`BaseScheduler.__init__` 删除 `alpha/beta`；`_record_migration` 用 `task.current_traffic` 作 task_load；`calculate_load(card)` 改为 `card.calculate_load()`。
4. baseline schedulers（[bestfit.py](../schedule/bestfit.py)、[roundrobin.py](../schedule/roundrobin.py)、[drf.py](../schedule/drf.py)、[p2c.py](../schedule/p2c.py)）：`record_physical_tick` 全部改为 `card_epoch_load[c.id] += sum(t.current_traffic for t in c.tasks)`，构造函数删除 `alpha/beta`。
5. [schedule/stps.py](../schedule/stps.py)：同上；删除 `alpha/beta`。
6. [schedule/placement_strategy.py](../schedule/placement_strategy.py)：删除 `alpha/beta`；P2C `weighted` 分支改为用 `_estimated_task_traffic(task) = mean(fp.E)` 作为新任务的预估贡献。
7. [simulation/engine.py](../simulation/engine.py)：
   - 删除 `alpha/beta/DEFAULT_ALPHA/DEFAULT_BETA`。
   - `_load_fingerprint_dir` 改为强约束：未提供 / 不存在 / 空目录 → 直接 raise，不再走 synthetic-fallback。
   - `_assign_fingerprint` 对**所有 scheduler** 都派发指纹（按 `task_id % len(paths)` 轮询，eager-load）。
   - 物理 tick 循环里的 phase-shift 分支显式 `task.current_traffic = 0.0` 后 `continue`，避免冻结上一 tick 的 E 值。
8. [util/metrics.py](../util/metrics.py)：`record_load_snapshot(time_step, cards, epoch_loads)` 改为必填 `epoch_loads`；删掉 `alpha/beta` 形参与瞬时回退分支。
9. [main.py](../main.py)：删除 `--alpha` / `--beta`；`--fingerprint-dir` 默认 `npz` 并对所有 scheduler 都生效（不再标 "STPS only"）。

### 9.2 与 Step B forecast 的同源衔接

- STPS Stage 2 仍然 `chosen.add_forecast(fp.E, offset)`（[schedule/stps.py:132](../schedule/stps.py#L132)）。
- 实际负载 = 各 task `E[(active_tick - 1) mod T]` 之和；offset 期间不计入，等价于"task 落卡后但还在静默"。这与 forecast 的 offset 偏移完全对应。
- baselines 的 `start_offset = 0`，task 一落卡即开始消费 `E[0..T-1]`。

### 9.3 验收（已通过）

- `pytest tests/` 32/32 全绿。
- 新增测试 [tests/test_simulation_integration.py](../tests/test_simulation_integration.py)：
  - `test_card_epoch_load_equals_E_sum_when_one_task_runs_one_period`：单 task 跑 `T` tick 后卡 epoch 累计 = `E.sum()` (atol=1e-4)。
  - `test_phase_shift_delay_zeros_traffic_until_offset_expires`：offset 期间 `current_traffic == 0`，offset 解除后第一个 tick 取 `E[0]`。
  - `test_missing_fingerprint_dir_raises`：删除指纹目录 → `FileNotFoundError`，不走静默回退。

### 9.4 后续校准（独立任务，未在此次重构内）

- 旧量纲 ≈ $\alpha\cdot 2000 + \beta\cdot 20000 \approx 2200$ /task/tick；新量纲取决于指纹 `E.mean()`：
  - `synthetic_flat.npz`：1.05
  - `synthetic_bursty.npz`：4.0
  - `synthetic_extreme.npz`：8.0
  - `spikingresformer_ti_imagenet100.npz`：≈1.16e12（远超合成档，混在目录里会主导，Q1 跑前需挪走或单独成实验）
- `script/q1_run.py` 里 `card_capacity=4500` 与 `util_to_tasks = {0.30:320, ..., 0.95:1100}` 全部按旧量纲，需要重新跑利用率扫描标定（可单写 `script/_calibrate.py`，本次不做）。

## 10. 开放问题（实施前需确认）

1. Q1 的 "STPS" 列采用 `stps-spatial`（无 Step B）
2. 核级负载在现有 `Card` 中是否已可直接拿到？若需重新建模 256-核占用，工作量翻倍。
3. 论文表格目标是 LaTeX 直接 `\input{}`（建议）还是手动复制？先拿到实验数据
4. 工作负载混合实验是否要求新指纹 `.npz`？现有 `make fingerprints` 只生成 3 个合成指纹，可能需扩展 [fingerprint/cli.py](../fingerprint/cli.py) 的 `--beta` 与 `--K` 扫描。工作负载先采用两种，possion + Steady-Flat 和 possion+ Sparse-Bursty
