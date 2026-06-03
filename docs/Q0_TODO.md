# Q0 实验设计：端到端卡间负载均衡度

> 对应 [article.tex](../article.tex) §6（端到端调度评估，先于 Q1/Q2/Q3 的拆分消融）。
> 目标：在完整的 STPS（3 stage）与所有 baseline 算法之间,比较**端到端**意义下
> **卡间负载均衡度**,检验 STPS 作为整套策略相对 RR / BestFit / DRF / P2C 的真实收益与边界。

## 1. 实验目标 & 与 Q1 的边界

Q0 是 **最外层** 的算法对比:不做 step 级消融,直接把每个 scheduler 作为**端到端黑盒**跑同一负载,看卡间负载均衡度。

| 维度 | Q0(本文档) | Q1([Q1_TODO.md](Q1_TODO.md)) |
|------|-----------|------------------------------|
| 对比集 | `{rr, bestfit, drf, p2c, stps}` | `{rr, bestfit, drf, p2c, stps-spatial}` |
| STPS 配置 | **完整 3 stage**(Stage A + Stage B phase-shift + Stage C hotspot split) | **仅 Step A** (无 phase shift) |
| 焦点 | 端到端策略整体效果 | 隔离 Step A 的空间放置增益 |
| 关注粒度 | **卡级** | 卡级 + 核级 |
| 评测窗口 | 稳态(去掉前 64 / 后 64 tick) | 同上 |

> 论述上 Q0 回答"完整 STPS 在端到端负载均衡上是否优于 baseline,优势出现在哪些负载/到达模式下",Q1 才进入"这里面有多少是 Step A 贡献的"。两者**不要共用** STPS 列。

## 2. 控制变量

| 维度 | 设定 |
|------|------|
| Scheduler | **变量**:`rr / bestfit / drf / p2c / stps` |
| 硬件 | 主表默认 $M=4$ 卡;大规模扩展实验 $M=16$ 卡;单卡容量见 §2.1 |
| 工作负载 | **主表使用同量纲 synthetic-only 指纹集合**;16 卡扩展实验使用 synthetic + real 混合指纹集合 |
| 步数 | `STEPS = 512` |
| 任务数 | 4 卡主表 `TASKS = 800`;16 卡大规模实验按卡数线性放大到 `TASKS = 3200`(保持约 70% 负载强度) |
| 指纹目录 | runner 按实验构造 synthetic-only 或 mixed 子目录;16 卡使用 synthetic + real 混合指纹集合 |
| 调度预测轨迹 | `mean_injection_trace`;所有 scheduler 的选卡/forecast 与 STPS Stage B phase-shift 都只使用 validation mean trace |
| 仿真测量轨迹 | `sample_measured_injection_trace`;任务真正开始执行后按单图片 ground-truth trace 产生卡上实测负载 |
| STPS 内部 knob | `bw_max=5e6, d_max=16, horizon=64, centrality_split_threshold=0.2` (4 卡与 16 卡同口径,除非专门做 `D_MAX` 敏感度) |
| Seed | `{21, 42, 99, 123, 2024}` 5 seeds,误差棒来源 |

> ⚠️ Q0 的"完整 STPS"必须用 scheduler name **`stps`**,不是 `stps-spatial` / `stps-temporal`。

### 2.1 16 卡集群配置与超参数

16 卡扩展实验使用与当前 `.env` / `script/q0_run.py` 一致的集群配置。其目的不是改变算法口径,而是在更大的候选卡集合和 mixed 指纹集合下验证 Q0 结论是否可扩展。

#### 集群硬件配置

| 参数 | 取值 | 来源 | 说明 |
|---|---:|---|---|
| `cards` | `16` | `script/q0_run.py` 的 `SCALE16_CARDS` | 16 张同构神经形态加速卡 |
| `CARD_TOTAL_CORES` | `16,777,216` | `.env` | 单卡可容纳神经元/核心上限;由原 `8,388,608` 翻倍,保证 `spikingresformer_ti_imagenet.npz` 可放置 |
| `CARD_TOTAL_SYNAPSES` | `16,777,216` | `.env` | 当前仿真中与 core/neuron 上限保持同量级 |
| `CARD_TOTAL_MEMORY_GB` | `128.0` | `.env` | 单卡内存保持 128GB 不变 |
| `CARD_BANDWIDTH_MBPS` | `1000.0` | `.env` | 单卡带宽配置;当前 Q0 的 STPS phase-shift 另用 `bw_max` 控制预测流量峰值 |

#### 16 卡 workload 配置

| 参数 | 取值 | 说明 |
|---|---|---|
| `tasks` | `3200` | 由 4 卡主表 `800 × 16 / 4` 线性放大,保持近似 per-card 负载强度 |
| `steps` | `512` | 与 4 卡主表一致 |
| `arrival_mode` | `{poisson, bursty}` | 16 卡只跑核心到达模式;`mixed` 已在 4 卡 arrival sweep 覆盖 |
| `seeds` | `{21, 42, 99, 123, 2024}` | 每个 scheduler / arrival 组合 5 个 seed |
| `fingerprint_set` | `mixed` | synthetic + real 混合指纹集合 |
| synthetic 指纹 | `synthetic_flat.npz`, `synthetic_pulse_t8.npz`, `synthetic_pulse_t16.npz`, `synthetic_bursty.npz` | 由 runner 构造/拷贝到临时 mixed 目录 |
| real 指纹 | `spikformer_cifar10.npz`, `qkformer_cifar10.npz`, `spikingresformer_ti_imagenet.npz` | 真实模型指纹;当前与合成指纹处于可比数量级,允许混用 |

#### STPS 超参数

| 参数 | 取值 | 代码位置 | 含义 |
|---|---:|---|---|
| `bw_max` | `5e6` | `script/q0_run.py` `_run_one()` | Stage B phase-shift 的预测流量峰值硬阈值;若所有 offset 下 peak 都超过该值,STPS 会标记 `bw_max_exceeded` |
| `d_max` | `16` | `script/q0_run.py` `_run_one()` | Stage B 可搜索的最大启动偏移 tick,也是 phase-shift 带来的最大等待窗口 |
| `horizon` | `64` | `script/q0_run.py` `_run_one()` | 每张卡维护的 forecast 窗口长度 |
| `centrality_split_threshold` | `0.2` | `script/q0_run.py` `_run_one()` | Stage C hotspot split 的中心性阈值 |
| `scheduler` | `stps` | `SCHEDULERS` | 使用完整 STPS,不是 `stps-spatial` / `stps-temporal` 消融 |

> 注意:`Makefile` 中也提供 `BW_MAX ?= 5e6`, `D_MAX ?= 16`, `HORIZON ?= 64` 作为手动运行 `main.py` 的默认值;Q0 runner 为保证可复现实验,在 `_run_one()` 中显式传入同一组参数。

#### 运行规模

16 卡扩展实验总运行数为:

$$
5\ \text{schedulers} \times 2\ \text{arrival modes} \times 5\ \text{seeds} = 50\ \text{runs}
$$

输出文件:

- `data/q0/scale16_raw.csv`:50 行实验记录 + header;
- `data/q0/scale16_summary.csv`:按 `fingerprint_set, arrival_mode, scheduler` 聚合,10 行结果 + header;
- `figures/q0/scale16_table.md`:论文/报告用 Markdown 表格。

## 3. 评测指标(卡间负载均衡度)

设 $L_{m,t}$ 为卡 $m \in \{1..M\}$ 在 tick $t$ 的样本级实测注入负载,即 [docs/load_TODO.md](load_TODO.md) §1-4 中定义的 `MeasuredInjectionLoad`。仿真执行时不再用 validation mean 直接评价负载,而是把卡上所有已真实启动任务的 `sample_measured_injection_trace` 按启动时刻叠加。逐 tick 计算后在 **稳态窗口** 内取算术平均。

| 指标 | 公式 | 期望方向 | 现状 |
|------|------|----------|------|
| **CV(变异系数)** | $\mathrm{CV}_t = \sigma(L_{\cdot,t}) / \mu(L_{\cdot,t})$ | ↓ | [util/metrics.py:59-67](../util/metrics.py#L59-L67) 已实现 `LoadSnapshot.cv` |
| **JFI(Jain's Fairness Index)** | $\mathrm{JFI}_t = (\sum_m L_{m,t})^2 / (M \sum_m L_{m,t}^2)$, $M=4$ | ↑(→1) | [util/metrics.py:69-80](../util/metrics.py#L69-L80) 已实现 `LoadSnapshot.jfi` |
| **LIF(Load Imbalance Factor,负载不平衡因子)** | $\mathrm{LIF}_t = \max_m L_{m,t} / \mu(L_{\cdot,t})$ | ↓(→1) | **需新增**:`LoadSnapshot.lif` |
| **Max/Min Ratio** | $\max_m L_{m,t} / \min_{m: L_{m,t}>0} L_{m,t}$ | ↓ | [util/metrics.py:82-88](../util/metrics.py#L82-L88) 已实现 |

每个 scheduler 报告 4 个基于 `MeasuredInjectionLoad` 的均衡度均值 ± 95% CI(across seeds)。如需要诊断 prediction 与 measured 的偏差,可额外保留基于 `mean_injection_trace` 的辅助列,但论文主表以 sample-measured 指标为准。

同时报告至少 1-2 个端到端保护指标,避免"靠延迟/吞吐损失换均衡"的误读:

| 指标 | 期望方向 | 用途 |
|------|----------|------|
| `completion_rate` | ↑ | 保证各 scheduler 完成任务比例可比 |
| `throughput` | ↑ | 检查 STPS 是否因延迟启动牺牲吞吐 |
| `p99_delay`(如当前 metrics 可直接输出) | ↓ | 作为端到端尾延迟保护指标 |

### 3.1 指标公式说明

- **负载口径**:$L_{m,t}$ 使用 `sample_measured_injection_trace` 叠加得到;`mean_injection_trace` 只用于 scheduler 预测与 phase-shift offset 搜索,避免优化目标和评价指标完全同源。
- **CV** 是负载方差的归一化版本,对 mean 不敏感,适合跨任务量/跨利用率横比。
- **JFI** 把均衡度压到 $(1/M, 1]$ 区间,完全均衡 = 1,最坏(单卡独占)= $1/M=0.25$。
- **LIF** 是 HPC 文献的常见指标,衡量最热卡相对平均的倍数,直接告诉读者"最差那张卡比平均多忙多少倍"。
- **Max/Min** 比 LIF 更激进(双端 outlier),用于交叉验证。

> 4 个指标在数学上不独立(完全均衡时全部退化),但实际数据上彼此互补:CV/JFI 反映**离散程度**,LIF/Max-Min 反映**尾端 outlier**。

## 4. 实验矩阵

### 4.1 主表(端到端比较)

固定 `cards=4, tasks=800, steps=512, arrival=bursty`,synthetic-only 指纹集合,5 schedulers × 5 seeds。

| Scheduler | Measured CV ↓ | Measured JFI ↑ | Measured LIF ↓ | Measured Max/Min ↓ |
|-----------|---------------|----------------|----------------|--------------------|
| RR        |      |       |       |           |
| BestFit   |      |       |       |           |
| DRF       |      |       |       |           |
| P2C       |      |       |       |           |
| **STPS**  |      |       |       |           |

主要检验点(不要预设 STPS 必须全面领先):
- STPS 的 CV/LIF 是否在 bursty 到达下降低,尤其相对 RR / P2C 是否显著改善;
- STPS 相对 BestFit / DRF 是否能反转 Q1 中 Step-A-only 的弱势;
- 若 STPS 未领先,需要定位是卡选择(Stage A)、phase-shift(Stage B)还是指纹分布导致。

### 4.2 到达模式敏感度(柱状图)

同样的 5 schedulers × 5 seeds,只换 `arrival_mode ∈ {poisson, bursty, mixed}`(3 列每指标),检验 STPS 端到端效果是否随到达过程变化。

预期:`poisson` 下 baseline / STPS 差距收窄;`bursty` 下 Step B 可能贡献更大;`mixed` 居中。若 `poisson` 下 STPS 变差,应作为边界条件写入 `Q0_result.md`。

### 4.3 任务数(负载强度)扫描(可选,折线图)

如时间允许:`tasks ∈ {320, 560, 800, 1020, 1100}`(对应 ~30% / 50% / 70% / 85% / 95% 利用率),每 scheduler 一条曲线,y 轴 CV。

> 这一节与 Q1 §4.2 共用 utilization 标定,如果都跑了,Q0 和 Q1 的对比图可以并排放进论文。

### 4.4 大规模扩展实验(16 卡)

目标:验证 Q0 结论是否能从默认 4 卡扩展到 16 卡,尤其观察 STPS 的 Stage B phase-shift 在更大候选卡集合下是否降低延迟代价、改善卡间均衡。

固定 `cards=16, tasks=3200, steps=512`,5 schedulers × 5 seeds,并覆盖 `arrival_mode ∈ {poisson, bursty}`。

指纹集合使用 synthetic + real 混合池:flat / pulse_t8 / pulse_t16 / bursty 合成指纹,以及真实模型指纹 `spikformer_cifar10.npz` / `qkformer_cifar10.npz` / `spikingresformer_ti_imagenet.npz`。当前这些指纹处于同一数量级,16 卡实验可以混用,以更接近端到端异构 workload。

| Arrival | Fingerprints | Scheduler | Measured CV ↓ | Measured JFI ↑ | Measured LIF ↓ | Measured Max/Min ↓ | Throughput ↑ | p99 delay ↓ |
|---------|--------------|-----------|---------------|----------------|----------------|--------------------|--------------|-------------|
| poisson | synthetic + real mixed | RR / BestFit / DRF / P2C / STPS | | | | | | |
| bursty | synthetic + real mixed | RR / BestFit / DRF / P2C / STPS | | | | | | |

主要检验点:
- STPS 在 16 卡下是否比 4 卡更容易找到低 peak offset,从而降低 `p99_delay` 和 throughput 损失;
- STPS 是否在 CV/JFI/LIF 上继续优于 RR / P2C,并是否能反转 BestFit / DRF;
- `poisson` 与 `bursty` 下 STPS 的延迟代价是否不同;
- synthetic + real 混合指纹下 phase-shift 的收益/代价是否仍稳定;
- 16 卡下 Max/Min 是否继续恶化;若恶化,需要区分是零负载卡数量增加还是少数热点卡导致;
- 与 4 卡主表并排报告 `STPS vs BestFit / DRF` 的差值变化,作为可扩展性结论。

产出文件建议:
- `data/q0/scale16_raw.csv`(包含 `arrival_mode` 与 `fingerprint_set=mixed` 列)
- `data/q0/scale16_summary.csv`(按 `arrival_mode, scheduler` 聚合;保留 `fingerprint_set=mixed` 作为 provenance)
- `figures/q0/scale16_table.md`

## 5. 需要的代码改动

> 控制范围:**只做 Q0 必需的改动**。Q1 已经把核级指标 / engine hook / Make 目标做完,这里只补全卡级 LIF + Q0 runner。

1. **指标扩展**:[util/metrics.py](../util/metrics.py)
   - `LoadSnapshot.lif` property:`max(loads) / mean(loads)`,mean=0 时返回 0。
   - `SimulationMetrics.avg_card_lif`:稳态窗口内 `lif` 的均值,沿用 `_steady_window()`。
   - `to_summary_dict` 增加 `card_lif` 字段;`write_summary_csv` 同步增加列。
2. **单元测试**:[tests/](../tests/) 新增 `test_load_lif_formula.py`,至少覆盖
   - 完美均衡(全 1.0)→ LIF=1.0;
   - 单热点(一卡独占)→ LIF=$M$;
   - 全零 → LIF=0(防 div-by-zero)。
3. **Q0 runner**:新建 [script/q0_run.py](../script/q0_run.py),复用 `q1_run.py` 的 `_run_one / _aggregate / _write_csv / _ci95`,核心差异是
   - `SCHEDULERS = ["rr", "bestfit", "drf", "p2c", "stps"]`(完整 stps,**不是** stps-spatial);
   - 调度预测与 STPS phase-shift 使用 `mean_injection_trace`;
   - 仿真执行时用 `sample_measured_injection_trace` 产生 `current_traffic`,等待 `start_offset` 时不消耗 trace、不产生流量;
   - 报告 4 个 sample-measured 均衡指标 `card_cv / card_jfi / card_lif / max_min_ratio`;
   - 同步输出 `completion_rate / throughput`(如已有 `p99_delay` 则加入 summary / markdown);
   - 输出 `data/q0/{main,arrival,sweep,scale16}_{raw,summary}.csv` + `figures/q0/{main,scale16}_table.md`。
4. **Make 目标**:[Makefile](../Makefile) 新增
   ```make
   q0:           ## Q0 主表:5 schedulers × 5 seeds × bursty
   q0-arrival:   ## Q0 到达模式敏感度(poisson/bursty/mixed)
   q0-sweep:     ## Q0 负载强度扫描(可选)
   q0-scale16:   ## Q0 大规模扩展:16 cards × 3200 tasks × 5 seeds × poisson/bursty × mixed fingerprints
   q0-all: q0 q0-arrival q0-scale16
   ```
5. **绘图(可选,论文阶段)**:`plot/q0_main.py` 把主表 CSV 渲染成柱状/表格;`plot/q0_arrival.py` 画到达模式分组柱状。先拿到数值再决定要不要写。

## 6. 产出物清单

- `data/q0/main_raw.csv` / `data/q0/main_summary.csv` — 主表原始 + 聚合数值
- `data/q0/arrival_raw.csv` / `arrival_summary.csv` — 到达模式敏感度
- (可选) `data/q0/sweep_raw.csv` / `sweep_summary.csv` — 利用率扫描
- `data/q0/scale16_raw.csv` / `scale16_summary.csv` — 16 卡大规模扩展实验
- `figures/q0/main_table.md` — 论文用的端到端对比表
- `figures/q0/scale16_table.md` — 16 卡扩展对比表
- `docs/Q0_result.md` — 数值与论文 claim 的对照清单(STPS vs RR/BestFit/DRF/P2C 各指标降幅 / 提升幅度),并明确写出 STPS 是否反转 BestFit / DRF

## 7. 验收标准(Definition of Done)

1. `make q0` 一键跑通主表 5 schedulers × 5 seeds,误差棒 < 5% 相对值。
2. 4 个指标全部基于 `sample_measured_injection_trace` 叠加出的 `MeasuredInjectionLoad` 计算,并写进 `card_cv / card_jfi / card_lif / max_min_ratio` 字段。
3. `docs/Q0_result.md` 必须客观报告 STPS 在 **bursty 到达** 下相对 RR / BestFit / DRF / P2C 的 CV/JFI/LIF/Max-Min 变化,不因结果不领先而删除实验。
4. 若 STPS 未全面优于 BestFit / DRF,必须给出边界解释:指纹分布、到达过程、Stage A 派发或 Stage B phase-shift 中至少定位一个可验证因素。
5. `make q0-arrival` 跑通 3 种到达模式,说明 STPS 优势/劣势在 `poisson / bursty / mixed` 下如何变化。
6. `make q0-scale16` 跑通 16 卡大规模扩展实验,覆盖 `poisson / bursty` 与 synthetic + real 混合指纹集合,生成 `scale16_raw.csv / scale16_summary.csv`,并在结果文档中按 `arrival_mode, scheduler` 聚合后与 4 卡主表对比。
7. `completion_rate / throughput` 不应显著劣于最强 baseline;若显著劣化,主结论必须降级为"均衡指标改善但端到端代价存在"。
8. `pytest tests/` 全绿,新指标 `lif` 至少有 3 个单元测试用例。

## 8. 与 Q1 的协同 / 防重复

- Q1 主表用 `stps-spatial`(隔离 Step A);Q0 主表用完整 `stps`。两表在论文中位置不同,数值不应混用。
- Q1 已落地的 `card_cv / card_jfi / max_min_ratio` 字段 Q0 直接复用,Q0 只新增 `card_lif`。
- Q1 的 utilization 标定(`util_to_tasks = {0.30:320, ..., 0.95:1100}`)在 Q0 §4.3 可选扫描里直接照搬,不重新标定。
- 16 卡实验按 4 卡主表的任务数线性放大(`800 × 16/4 = 3200`),目的是保持近似相同 per-card 负载强度;不要把 16 卡结果与 4 卡结果直接按绝对 throughput 解读,应比较 normalized load-balance 指标和 p99 delay 变化。
- Q0 主表**固定使用 synthetic-only 子集**以保持与小规模结果可比;16 卡扩展实验使用 synthetic + real 混合指纹集合。当前纳入的真实模型指纹与合成指纹处于同一数量级,可混用;若未来加入数量级差异明显的新指纹,需单独标注归一化策略。

## 9. 时间线(建议)

| 阶段 | 内容 | 预估 |
|------|------|------|
| 1 | LIF 公式 + 单元测试 | 0.5 天 |
| 2 | Q0 runner + Makefile + 主表跑通 | 0.5 天 |
| 3 | 到达模式敏感度 + `Q0_result.md` | 0.5 天 |
| 4 | 16 卡扩展实验 + `scale16_table.md` | 0.5 天 |
| 5 | (可选)利用率扫描 + 绘图 | 0.5 天 |

## 10. 开放问题(实施前需确认)

1. Q0 主表默认采用 `arrival_mode=bursty`;`poisson / mixed` 放入到达模式敏感度。
2. Q0 主表不混入真实模型指纹以保持小规模主表口径稳定;16 卡扩展实验混用 synthetic + real 指纹。
3. 端到端保护指标纳入主表附加列:至少 `completion_rate / throughput`,如 `p99_delay` 当前 metrics 可直接输出则一并报告。
4. 16 卡扩展实验默认使用 `tasks=3200, steps=512, arrival_mode ∈ {poisson, bursty}, fingerprint_set=mixed`;若运行时间过长,优先保留 5 seeds × 5 schedulers 的两组核心 arrival 组合,把额外 sweep 作为后续实验。
5. Q0 结果解释必须区分预测轨迹与实测轨迹:算法优化的是 `mean_injection_trace` 的 forecast,主指标评价的是 `sample_measured_injection_trace` 的单图片 ground-truth 回放。
