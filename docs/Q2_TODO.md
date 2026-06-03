# Q2 实验设计：Step B Phase-Shift 垂直消融

> 目标：把 **Step B / phase-shift** 从 STPS 中抽离成一个可复用 temporal wrapper，分别叠加到 RR、BestFit、DRF、P2C 和 STPS 空间策略上，做每个算法自己的垂直对比：`Algo(+phase-shift)` vs `Algo`。
>
> 核心问题不是“STPS 是否优于 baseline”，而是：**同一个空间放置策略在加入 phase-shift 后，牺牲了多少吞吐 / 延迟，换取了多少负载均衡收益。**

## 1. 实验问题

Q2 回答 Step B 的独立贡献：

1. Phase-shift 是否对所有空间调度策略都能改善卡间负载均衡？
2. 不同基础算法上，phase-shift 的收益是否一致？
3. Phase-shift 的代价主要体现为 throughput 降低、p99 delay 增加，还是 completion rate 下降？
4. STPS 当前端到端收益中，有多少来自 Step B，而不是 Step A / Step C？

## 2. 对比矩阵

每个算法只和自己的 `+phase-shift` 版本比较，避免横向算法差异掩盖 Step B 的净效应。

| 基础策略 | + phase-shift 版本 | 对比含义 |
|---|---|---|
| `rr` | `rr-phase` | 轮询放置不变，只增加启动 offset 搜索 |
| `bestfit` | `bestfit-phase` | BestFit 选卡口径不变，只增加 temporal smoothing |
| `drf` | `drf-phase` | DRF 公平性放置不变，只增加 temporal smoothing |
| `p2c`  | `p2c-phase` | 两随机候选策略不变，只增加 temporal smoothing |
| `stps-spatial` | `stps` | STPS 的 Step A + Step C 基线，对比完整 STPS 的 Step B 增量 |



## 3. 实验原则

### 3.1 保持空间放置策略不变

`Algo(+phase-shift)` 必须先调用原算法的选卡逻辑，选出 candidate card 后，只在该 card 上搜索 `start_offset`。这样可以隔离 Step B：

$$
\Delta_{B}(Algo) = Metric(Algo + PhaseShift) - Metric(Algo)
$$

不要让 `+phase-shift` 重新在所有卡之间按 peak 选择，否则会混入新的 card-selection 逻辑，无法说明 Step B 的独立贡献。

### 3.2 只允许改变启动时间

`+phase-shift` 版本允许改变：

- `task.start_offset`
- card forecast timeline
- phase-shift 相关拒绝原因，例如 `bw_max_exceeded`

不允许改变：

- 原算法的候选卡选择规则
- task resource demand
- fingerprint traffic sequence
- arrival plan
- simulation steps
- card capacity

### 3.3 生命周期口径

任务等待 offset 时不能消耗 `duration_steps`。Q0 已修复该 bug，Q2 继续沿用：只有任务真正开始执行后，`duration_steps` 才递减。否则 phase-shift 会被错误惩罚，吞吐 / completion 指标不可信。

## 4. 实验配置

Q2 分成 **4 卡主实验** 和 **16 卡扩展实验** 两组。4 卡主实验用于和 Q0/Q1 的小规模主体结果对齐，便于快速迭代和定位 phase-shift 的净效应；16 卡扩展实验用于验证 Step B 在更大候选卡集合、mixed 指纹和更高任务规模下是否仍然成立。

两组实验共享同一套基础算法、phase 版本、seed、生命周期口径和 phase-shift 超参数，只改变集群规模、任务数和指纹集合。

### 4.1 共同配置

| 维度 | 取值 |
|---|---|
| steps | `512` |
| arrival_mode | `{poisson, bursty}` |
| seeds | `{21, 42, 99, 123, 2024}` |
| 基础算法 | `rr / bestfit / drf / p2c / stps-spatial` |
| phase 版本 | `rr-phase / bestfit-phase / drf-phase / p2c-phase / stps` |
| 单卡容量 | `CARD_TOTAL_CORES = 16,777,216`，`CARD_TOTAL_SYNAPSES = 16,777,216` |
| 单卡内存 | `CARD_TOTAL_MEMORY_GB = 128.0` |
| phase 变量 | 仅允许改变 `start_offset` 与 forecast timeline |

### 4.2 4 卡主实验

| 维度 | 取值 | 说明 |
|---|---:|---|
| cards | `4` | 与 Q0 4 卡主表对齐 |
| tasks | `800` | 与 Q0 4 卡主表一致，保持约相同 per-card 负载强度 |
| fingerprint_set | `mixed` | synthetic + real 混合指纹集合，与 16 卡扩展实验保持同一 workload 口径 |
| arrival_mode | `{poisson, bursty}` | 同时覆盖平滑到达和突发到达 |
| 输出前缀 | `main4` | 建议输出到 `data/q2/main4_*` 和 `figures/q2/main4_*` |

4 卡主实验运行数：

$$
5\ \text{base algorithms} \times 2\ \text{phase settings} \times 2\ \text{arrival modes} \times 5\ \text{seeds} = 100\ \text{runs}
$$

### 4.3 16 卡扩展实验

| 维度 | 取值 | 说明 |
|---|---:|---|
| cards | `16` | 与 Q0 16 卡扩展实验对齐 |
| tasks | `3200` | 由 4 卡 `800 × 16 / 4` 线性放大 |
| fingerprint_set | `mixed` | synthetic + real 混合指纹集合 |
| arrival_mode | `{poisson, bursty}` | 与 4 卡主实验保持一致 |
| 输出前缀 | `scale16` | 建议输出到 `data/q2/scale16_*` 和 `figures/q2/scale16_*` |

16 卡扩展实验运行数：

$$
5\ \text{base algorithms} \times 2\ \text{phase settings} \times 2\ \text{arrival modes} \times 5\ \text{seeds} = 100\ \text{runs}
$$

Q2 全量运行数：

$$
100\ \text{4-card runs} + 100\ \text{16-card runs} = 200\ \text{runs}
$$

### 4.4 结果解读边界

- 4 卡结果作为主表：优先用于说明 Step B 在 mixed 指纹负载下的小规模收益 / 代价。
- 16 卡结果作为扩展表：优先用于说明 Step B 在同一 mixed 指纹口径、更大集群下是否保持相同趋势。
- 不直接用 4 卡和 16 卡的绝对 throughput 做结论；跨规模比较应看 normalized load-balance 指标和 phase-shift 代价比例。
- 若 4 卡和 16 卡趋势冲突，优先检查 `mean_start_offset / p95_start_offset / reject_rate_bw`，判断差异来自 offset 分布、BW 约束还是 mixed 指纹。

## 5. Phase-Shift 超参数

所有 `+phase-shift` 版本统一使用 Q0 中 STPS 的 Step B 参数，避免引入额外调参变量。

| 参数 | 取值 | 说明 |
|---|---:|---|
| `bw_max` | `5e6` | 预测 traffic peak 上限 |
| `d_max` | `16` | 最大启动 offset tick |
| `horizon` | `64` | 每张卡 forecast 窗口 |
| `phase_objective` | `min_peak` | 在 `[0, d_max]` 内最小化 `forecast + shifted(E)` 的 peak |
| `reject_policy` | `same_as_stps` | 若所有 offset 都超过 `bw_max`，按当前 STPS 口径处理 |

后续可选敏感度实验：

$$
D_{max} \in \{0, 2, 4, 8, 16\}
$$

其中 `D_max=0` 应退化为不做 phase-shift，可作为实现正确性的 sanity check。

## 6. 指标设计

### 6.1 负载均衡收益

对每组 `Algo(+phase-shift)` vs `Algo` 计算：

| 指标 | 方向 | 解释 |
|---|---|---|
| `card_cv` | 越低越好 | 卡间负载变异系数 |
| `card_jfi` | 越高越好 | Jain fairness index |
| `card_lif` | 越低越好 | 负载热点放大系数 |
| `max_min_ratio` | 越低越好 | 最重卡 / 最轻非零卡比值 |

报告相对变化：

$$
\mathrm{CV\ Gain} = \frac{CV_{base} - CV_{phase}}{CV_{base}}
$$

$$
\mathrm{JFI\ Gain} = JFI_{phase} - JFI_{base}
$$

$$
\mathrm{LIF\ Gain} = \frac{LIF_{base} - LIF_{phase}}{LIF_{base}}
$$

### 6.2 端到端代价

| 指标 | 方向 | 解释 |
|---|---|---|
| `throughput` | 越高越好 | 单位 step 完成任务量 |
| `completion_rate` | 越高越好 | 任务完成率 |
| `p99_delay` | 越低越好 | phase-shift 最可能恶化的尾延迟指标 |
| `mean_start_offset` | 越低越好 | 平均等待 offset，用于解释 delay 增长 |
| `p95_start_offset` | 越低越好 | offset 尾部，用于定位过度等待 |
| `reject_rate_bw` | 越低越好 | 因 `bw_max_exceeded` 拒绝的比例 |

报告代价：

$$
\mathrm{Throughput\ Cost} = \frac{Throughput_{base} - Throughput_{phase}}{Throughput_{base}}
$$

$$
\mathrm{P99\ Delay\ Cost} = \frac{P99_{phase} - P99_{base}}{P99_{base}}
$$

## 7. 输出表格

### 7.1 主表：每个算法垂直对比

4 卡主表建议输出到 `figures/q2/main4_vertical_table.md`，16 卡扩展表建议输出到 `figures/q2/scale16_vertical_table.md`：

| Arrival | Base Algo | CV Δ% ↓ | JFI Δ ↑ | LIF Δ% ↓ | Throughput Δ% | p99 Delay Δ% | mean offset | 结论 |
|---|---|---:|---:|---:|---:|---:|---:|---|
| poisson | RR |  |  |  |  |  |  |  |
| poisson | BestFit |  |  |  |  |  |  |  |
| poisson | DRF |  |  |  |  |  |  |  |
| poisson | P2C |  |  |  |  |  |  |  |
| poisson | STPS-spatial |  |  |  |  |  |  |  |
| bursty | RR |  |  |  |  |  |  |  |
| bursty | BestFit |  |  |  |  |  |  |  |
| bursty | DRF |  |  |  |  |  |  |  |
| bursty | P2C |  |  |  |  |  |  |  |
| bursty | STPS-spatial |  |  |  |  |  |  |  |

### 7.2 Pareto 表：均衡收益 / 吞吐代价

4 卡 Pareto 表建议输出到 `figures/q2/main4_pareto_table.md`，16 卡 Pareto 表建议输出到 `figures/q2/scale16_pareto_table.md`：

| Base Algo | Arrival | CV Gain | Throughput Cost | p99 Cost | 是否 Pareto 有效 |
|---|---|---:|---:|---:|---|

用于判断 Step B 是否值得：如果某算法 `CV Gain` 很小但 `p99 Cost` 很大，则 phase-shift 对该算法不是好的 trade-off。

## 8. 需要的代码改动

1. **抽象 phase wrapper**：新增可复用的 phase-shift mixin 或 wrapper，避免复制 STPS 代码。
   - 输入：基础 scheduler 已选出的 `Card`、task fingerprint、card forecast。
   - 输出：`task.start_offset`、forecast update、是否因 `bw_max` reject。
2. **新增 scheduler variants**：注册 `rr-phase / bestfit-phase / drf-phase / p2c-phase`。
   - `stps` 已经是 `stps-spatial + phase-shift`，不需要新增 `stps-phase`。
3. **指标扩展**：记录每个任务的 `start_offset`，在 summary 中输出 `mean_start_offset / p95_start_offset / reject_rate_bw`。
4. **Q2 runner**：新增 `script/q2_run.py`。
   - 输出 `data/q2/main4_raw.csv` / `data/q2/main4_summary.csv`
   - 输出 `data/q2/scale16_raw.csv` / `data/q2/scale16_summary.csv`
   - 输出 `figures/q2/main4_vertical_table.md` / `figures/q2/scale16_vertical_table.md`
   - 输出 `figures/q2/main4_pareto_table.md` / `figures/q2/scale16_pareto_table.md`
5. **Makefile 目标**：新增

```makefile
q2-main4:   ## Q2 4-card main vertical ablation
q2-scale16: ## Q2 16-card scale vertical ablation
q2: q2-main4 q2-scale16
q2-smoke:   ## Small Q2 smoke test for scheduler variants
```

## 9. 实现验收标准

1. `main.py --list-schedulers` 能看到 `rr-phase / bestfit-phase / drf-phase / p2c-phase`。
2. `q2-smoke` 在小规模配置下跑通全部 10 个 scheduler 条目：5 个 base + 5 个 phase 对照。
3. `D_MAX=0` 时，`Algo(+phase-shift)` 的 `start_offset` 全为 0，负载指标应接近 base 算法。
4. `D_MAX=16` 时，至少部分任务的 `start_offset > 0`，否则 phase-shift 没有实际生效。
5. Q2 主实验生成 `data/q2/main4_summary.csv`，16 卡扩展生成 `data/q2/scale16_summary.csv`；每个规模、每个 arrival 下包含 10 行：5 个 base + 5 个 phase。
6. `docs/Q2_result.md` 必须按算法垂直报告收益和代价，不允许只报告 STPS 总体最优。
7. `pytest tests/` 通过；新增测试至少覆盖 phase wrapper、scheduler 注册、offset lifecycle 不消耗任务执行时长。

## 10. 论文表述建议

Q2 的 claim 应写成 trade-off，而不是单向最优：

> Step B phase-shift is a reusable temporal smoothing module. Across RR, BestFit, DRF, R2C and STPS-spatial, it reduces card-level load variance by delaying task starts within a bounded window, but the improvement comes at the cost of lower effective throughput and higher tail latency. The magnitude of this trade-off depends on the base spatial placement policy and the burstiness of arrivals.

中文解释：Step B 是“时间平滑器”，不是新的空间选卡算法。它应被评价为“用可控等待换负载均衡”，核心结果是每个算法自己的收益 / 代价曲线。

## 11. 与 Q0 / Q1 的边界

- Q0：比较完整端到端调度器，回答 `STPS vs baselines`。
- Q1：隔离 Step A / Step C 的空间放置收益，使用 `stps-spatial`。
- Q2：隔离 Step B phase-shift 的时间平滑收益，使用 `Algo(+phase-shift) vs Algo` 的垂直对比，并同时报告 4 卡主表与 16 卡扩展表。
- Q2 不应复用 Q0 的横向结论；Q2 的主图应该是每个 base algorithm 的 before/after pair。
