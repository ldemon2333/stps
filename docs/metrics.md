# Metrics 计算方式

> 关联代码：[util/metrics.py](../util/metrics.py)、[simulation/engine.py](../simulation/engine.py)。
> 关联文档：[Q0_result.md](Q0_result.md)、[traffic_result.md](traffic_result.md)、[traffic_optim.md](traffic_optim.md)。
>
> 本文档逐项给出 Q0 / traffic 实验里出现的 metric 的精确计算口径，回答 “这个数到底是怎么算出来的” —— 不是物理解释，是公式 + 代码定位。

---

## 0. 通用约定

- **快照粒度**：每个 simulation tick 产生 1 个 `LoadSnapshot` ([util/metrics.py:36](../util/metrics.py#L36))。所有 *card-level* metric 都是先在快照内对 N 张卡聚合成 1 个标量，再沿 tick 维度做平均。
- **Steady-state 窗口**：当快照数 > 128 时，丢弃首 64 + 末 64 个 tick，只用中间窗口聚合（[util/metrics.py:213](../util/metrics.py#L213) `_steady_window`）。Q0 / traffic 默认 512 ticks → 实际窗口 384 ticks。≤128 时不裁剪。
- **`card_load`（per-card per-tick）**：等于该 tick 这张卡 *实际服务* 的总字节数 `Σ_tasks_on_card served`（[engine.py:353](../simulation/engine.py#L353) `_card_epoch_load`），即被 `bw_cap` 钳制后的值，**不包括** 入 `pending_traffic` 的 leftover。
- **`card_demand`（per-card per-tick）**：该 tick 这张卡上 *请求* 的总字节数（含队列残量 + 新 quantum），= `Σ_tasks demand`，未受 `bw_cap` 钳制。
- **`card_backlog`（per-card per-tick）**：该 tick 结束后这张卡上所有任务 `pending_traffic` 之和。

---

## 1. 空间均衡类（卡间累计负载离散度）

这些 metric 全部用 `card_load`（served），在每个快照内对 N 张卡算一次，再沿 tick 做平均。**衡量的是“卡之间总活儿分得平不平”，与时间维错峰无关。**

### 1.1 `card_cv` — Coefficient of Variation

```text
per-tick:  cv_t = std(L_{i,t}) / mean(L_{i,t})         # L_{i,t} = card i 在 tick t 的 served
report:    card_cv = mean_{t ∈ steady} cv_t
```

- 代码：[util/metrics.py:64-73](../util/metrics.py#L64-L73) (`LoadSnapshot.cv`) → [util/metrics.py:220](../util/metrics.py#L220) (`avg_card_cv`)
- mean ≤ 0 时该 tick 返回 0；卡数 < 2 时返回 0。
- **越小越均衡**。

### 1.2 `card_jfi` — Jain's Fairness Index

```text
per-tick:  jfi_t = (Σ L_{i,t})² / (N · Σ L_{i,t}²)     # N = 卡数
report:    card_jfi = mean_{t ∈ steady} jfi_t
```

- 代码：[util/metrics.py:75-86](../util/metrics.py#L75-L86) → [util/metrics.py:227](../util/metrics.py#L227)
- 范围 (0, 1]，**越大越均衡**；全部为 0 时返回 1.0。

### 1.3 `card_lif` — Load Imbalance Factor

```text
per-tick:  lif_t = max_i L_{i,t} / mean_i L_{i,t}
report:    card_lif = mean over { t ∈ steady : lif_t > 0 }
```

- 代码：[util/metrics.py:88-96](../util/metrics.py#L88-L96) → [util/metrics.py:234](../util/metrics.py#L234)
- **越小越均衡**，下界 1.0（完全均衡）。
- 注意：`avg_card_lif` 只对 `lif_t > 0` 的 tick 取均值，全空载 tick 不参与。

### 1.4 `max_min_ratio` — Max/Min 比

```text
per-tick:  ratio_t = max(L_{i,t}) / min_{L_{i,t} > 0}(L_{i,t})   # 跳过零负载卡
report:    max_min_ratio = mean over { t ∈ steady : ratio_t > 0 }
```

- 代码：[util/metrics.py:98-104](../util/metrics.py#L98-L104) → [util/metrics.py:242](../util/metrics.py#L242)
- **越小越均衡**。剔除零负载卡是为避免除零；当 N=16 时，少数 tick 有零负载卡，分母只取正项。
- 这是 [Q0_result.md §4](Q0_result.md) 看到 STPS 在 16-card 反而更优的指标 —— 它对“尾点过载”最敏感。

### 1.5 `avg_load_imbalance` — Load Variance 平均

```text
per-tick:  var_t = Σ_i (L_{i,t} − mean)² / N           # 非 N-1
report:    avg_load_imbalance = mean_{t ∈ all_snapshots} var_t   # 注意：不裁 steady window
```

- 代码：[util/metrics.py:56-62](../util/metrics.py#L56-L62) → [util/metrics.py:199](../util/metrics.py#L199)
- 单位是 `(B/tick)²`，因此数值通常很大（Q0 表里 1e10 量级）。
- **注意**：`avg_load_imbalance` 与上面 4 个不同，**不裁剪 steady window**，含 warmup/teardown。

---

## 1b. 时间维负载均衡类（单卡 T 序列内离散度）

> **设计目的**：与 §1 的空间维（across cards, per-tick）正交。§1 衡量 “某一 tick 上 N 张卡是否分得均”，**§1b 衡量 “某一张卡在自己 T 个 tick 上是否打得均”** —— 即单卡时间轴上的负载是否平滑还是 spikey。
>
> **样本空间**：对每张卡 `i`，取它在 steady-state 窗口内的 served 序列 `L_i = (L_{i, t_0}, L_{i, t_0+1}, …, L_{i, t_1})`，长度 `T = t_1 − t_0 + 1`（Q0 默认 384）。
>
> **报告方式**：每张卡得到一个标量；**有 N 张卡就有 N 个独立的时间维负载均衡值**，不预先在卡之间平均。汇总时既给 *per-card 向量*（写入 raw CSV 一列一张卡），也给 *cross-card 摘要*（mean / std / max / min / median 五个标量进 summary CSV，便于跨调度器横比）。
>
> **与 §1 的对比口诀**：
>
> - §1 = `time-of(space-stat(L_{i,t}))` —— 先 across cards 算离散度，再沿 time 取均值。
> - §1b = `space-of(time-stat(L_{i,t}))` —— 先沿 time 算离散度（per card），再用 N 个值描述卡间分布。
>
> 两者用同一份 `card_load` 三维数据，仅聚合顺序相反。STPS 的 Stage B 错峰直接作用在 *单卡 T 序列* 上，所以 §1b 才是 STPS 的“主场”指标。

### 1b.1 `time_card_cv` — 单卡时间序列 CV

```text
per-card:   tcv_i = std_t(L_{i,t}) / mean_t(L_{i,t})            t ∈ steady window
report  :   时间序列上的“起伏程度”，每卡一个标量
summary :   time_card_cv_mean   = mean_i tcv_i
            time_card_cv_max    = max_i tcv_i        # 抖得最厉害的那张卡
            time_card_cv_min    = min_i tcv_i        # 最平的那张卡
            time_card_cv_std    = std_i tcv_i        # 卡之间的“抖动差异”
            time_card_cv_median = median_i tcv_i
```

- mean_t ≤ 0 → 该卡返回 0（卡全程空载，无意义）。
- **越小越平滑**。STPS Stage B 错峰若有效，应直接降低 `time_card_cv_mean` 与 `time_card_cv_max`。
- 与 §1.1 区分：§1.1 `card_cv` 是“某一 tick 上卡间离散”，1b.1 是“某一张卡 T 个 tick 内时间离散”。

### 1b.2 `time_card_jfi` — 单卡时间序列 Jain's Fairness

```text
per-card:   tjfi_i = (Σ_t L_{i,t})² / (T · Σ_t L_{i,t}²)        T = len(steady window)
summary :   mean/max/min/std/median over cards
```

- 范围 (0, 1]；**越大越平滑**（全均匀时 → 1.0，全集中在 1 个 tick → 1/T）。
- 全卡全程 0 → 该卡返回 1.0。

### 1b.3 `time_card_lif` — 单卡时间序列 Load Imbalance Factor

```text
per-card:   tlif_i = max_t(L_{i,t}) / mean_t(L_{i,t})
summary :   mean/max/min/std/median over cards
```

- 物理含义：**单卡 spike 高度相对自己平均负载的倍数**。一张卡如果整段稳定打满 → tlif≈1；如果只在某几个 tick 打 spike，其余 idle → tlif >> 1。
- mean_t ≤ 0 的卡不参与 cross-card 摘要（与 §1.3 同源逻辑）。
- **越小越平滑**，下界 1.0。
- 这是与 NoC 拥塞 (`avg_congestion_ratio`) 关联最直接的指标 —— spike 高度越高，越容易超 `bw_cap`。

### 1b.4 `time_card_max_min_ratio` — 单卡时间序列 Max/Min

```text
per-card:   tmm_i = max_t(L_{i,t}) / min_{L_{i,t} > 0}(L_{i,t})    跳过零负载 tick
summary :   mean/max/min/std/median over cards (skip card if no positive tick)
```

- 物理含义：单卡负载的“峰谷比”。
- **越小越平滑**，下界 1.0。零负载 tick 全程被剔除，避免 `min=0` 把比值打到无穷。

### 1b.5 `time_card_load_variance` — 单卡时间序列 Load Variance

```text
per-card:   tvar_i = Σ_t (L_{i,t} − mean_t)² / T
summary :   mean/max/min/std/median over cards
```

- 单位 `(B/tick)²`；保留是为与 §1.5 `avg_load_imbalance` 同口径做对照（一个是“across cards 取 var”，这里是“along time 取 var”）。
- **越小越平滑**。

### 1b.6 实现与输出约定

- **聚合代码位置**：拟新增 `SimulationMetrics.time_card_*` 系列 properties，输入 `_steady_window()` 的 snapshots，先按 `card_id` 重排成 `{card_id: List[load]}`，再对每张卡用 numpy 计算 cv/jfi/lif/maxmin/var。
- **Summary CSV 增列**（每个指标贡献 5 个标量）：
  `time_card_cv_{mean,max,min,std,median}`, `time_card_jfi_{...}`, `time_card_lif_{...}`, `time_card_max_min_ratio_{...}`, `time_card_load_variance_{...}` —— 共 25 个新列。
- **Per-card 详细列**写入 `*_loads_*.csv` 同目录的 `*_time_balance_*.csv` 旁路文件，每行 `(card_id, tcv, tjfi, tlif, tmm, tvar)`，N 行。
- **Steady window 一致**：与 §1 / §2 完全相同 (skip 64 head/tail when snapshots > 128)。
- **优劣方向汇总**：cv ↓、jfi ↑、lif ↓、max_min_ratio ↓、load_variance ↓。

### 1b.7 与 STPS 的关联预期

- **time_card_lif_mean** 期望：STPS < baseline（Stage B 把同卡上多任务的 spike 错开 → 单卡 spike 高度降）。
- **time_card_cv_max** 期望：STPS < baseline（最 spike 的那张卡被压平最多）。
- **time_card_jfi_mean** 期望：STPS > baseline。
- 同时 §1 的 `card_cv`（across cards）期望维持或略高 —— 这正是 [Q0_result.md §6](Q0_result.md) 里 “拥塞降但空间均衡未降” 的指标级解释：STPS 改善的是 §1b 而不是 §1。

---

## 2. 时间维拥塞类（per-tick bandwidth contention）

这些 metric 来自 `bw_cap` 钳制后的 `(demand, served, backlog)` 三元组，定义在 [engine.py:344-378](../simulation/engine.py#L344-L378)，聚合在 [util/metrics.py:254-339](../util/metrics.py#L254-L339)。**衡量的是“tick 级 spike 有没有撞 cap”。**

### 2.1 `avg_congestion_ratio`

```text
per-card per-tick:  cong_{i,t} = (demand_{i,t} − served_{i,t}) / demand_{i,t}    if demand > 0
                                = 0                                              otherwise
report:             avg_congestion_ratio = mean over all (i, t) in steady window
```

- 代码：[util/metrics.py:178-181](../util/metrics.py#L178-L181) 算每卡每 tick 的 cong → [util/metrics.py:254](../util/metrics.py#L254) 平铺平均。
- 范围 [0, 1)，**越小越好**。
- 当 `demand ≤ bw_cap` 时 served=demand → ratio=0；溢出时 ratio = (1 − scale) = (demand − bw_cap)/demand。

### 2.2 `congested_card_tick_frac`

```text
report = # { (i, t) ∈ steady : cong_{i,t} > 1e-9 } / # { (i, t) ∈ steady }
```

- 代码：[util/metrics.py:272-284](../util/metrics.py#L272-L284)
- 拥塞 (i,t) 的占比 ∈ [0, 1]；**越小越好**。
- 与 `avg_congestion_ratio` 区别：前者衡量 “拥塞窗口的高度”（含 0 的均值），后者衡量 “拥塞窗口的宽度”。

### 2.3 `peak_backlog`

```text
report = max over (i, t) ∈ steady  card_backlog_{i,t}
       = max  Σ_tasks_on_card_i_at_t  task.pending_traffic
```

- 代码：[util/metrics.py:265-270](../util/metrics.py#L265-L270)
- 单位 B；steady 窗口内单卡 backlog 的最大值。
- **越小越好**，对 STPS 不一定占优（[Q0_result.md §5](Q0_result.md) §9.4：STPS 强在持续时间不在峰值）。

### 2.4 `avg_utilization`

```text
per-card per-tick:  util_{i,t} = served_{i,t} / bw_cap                 if bw_cap > 0
                              = 0                                      otherwise
report:             avg_utilization = mean over all (i, t) in steady
```

- 代码：[util/metrics.py:182-185](../util/metrics.py#L182-L185), [util/metrics.py:286-295](../util/metrics.py#L286-L295)
- 没有 `bw_cap` 时返回 0；范围 [0, 1]；**越大越好**（在 completion_rate=1 的前提下）。

### 2.5 `mean_congestion_wait_ticks` / `p95_congestion_wait_ticks`

```text
per-task lifetime counter:  task.congestion_wait_ticks += 1  each tick the task has leftover
report mean:  mean over all completed/recorded tasks
report p95 :  numpy.percentile(values, 95)
```

- 代码：每 tick 在 [engine.py:366](../simulation/engine.py#L366) 累加 → 任务完成时 [engine.py:451](../simulation/engine.py#L451) push 进 `metrics.congestion_wait_ticks` → [util/metrics.py:329-339](../util/metrics.py#L329-L339) 聚合。
- 单位 ticks，**越小越好**。
- 注意它是 **任务级** 统计（每完成 1 任务 1 个样本），不是 tick 级。

### 2.6 `avg_demand_cv` / `avg_backlog_cv`

```text
avg_demand_cv  = mean_{t ∈ steady} std_i(card_demand_{i,t})  / mean_i(card_demand_{i,t})
avg_backlog_cv = mean_{t ∈ steady} std_i(card_backlog_{i,t}) / mean_i(card_backlog_{i,t})
```

- 代码：[util/metrics.py:297-327](../util/metrics.py#L297-L327)
- 各自衡量 “demand / backlog 在卡之间是否平均”；与 §1 的 `card_cv` 是 served 维度，三者并列。

### 2.7 `congestion_timeouts`

```text
report = Σ #{ tick at which task.blocked_ticks > MAX_BACKLOG_TICKS, forcing drain }
```

- 代码：[engine.py:369-373](../simulation/engine.py#L369-L373) 触发 → `metrics.congestion_timeouts` 累加。
- Q0 / traffic 中实测 = 0 → completion_rate 才能 1.000。

---

## 3. 延时类（per-task arrival → completion）

每个完成的任务推一个 `TaskDelay` 记录 ([util/metrics.py:20-33](../util/metrics.py#L20-L33))，`total_delay = completion_step − arrival_step`。**注意是 `arrival → completion`，含 placement 等待与 NoC 排队**，不只算执行时间。

```text
delays = [ d.total_delay for d in task_delays if d.total_delay >= 0 ]    # 过滤未完成

p50_delay  = numpy.percentile(delays, 50)
p95_delay  = numpy.percentile(delays, 95)
p99_delay  = numpy.percentile(delays, 99)
avg_delay  = numpy.mean(delays)
max_delay  = max(delays)
```

- 代码：[util/metrics.py:356-404](../util/metrics.py#L356-L404)
- 单位 ticks，**越小越好**。
- **重要警告**：当 `completion_rate < 1.0` 时，这些分位数只覆盖 *完成* 的任务，未完成的高延时任务不在样本里 —— 此时不能直接拿来跨调度器比较。Q0 已保证 `completion_rate=1.000`，故可比；traffic_result.md §4 也强调过此点。

---

## 4. 吞吐 / 完成率类

### 4.1 `throughput`

```text
throughput = tasks_completed / total_snapshots
           = tasks_completed / steps                # snapshots 数 = simulate 的 tick 数
```

- 代码：[util/metrics.py:341-347](../util/metrics.py#L341-L347)
- 单位 tasks/tick；**越大越好**。
- 注意分母是 **全段 tick 数**（含 warmup），不裁 steady window。

### 4.2 `completion_rate`

```text
completion_rate = tasks_completed / task_count
```

- 代码：[util/metrics.py:349-354](../util/metrics.py#L349-L354)
- 范围 [0, 1]；**主报指标，必须先看它**。Q0 / traffic 全部 = 1.000 → 可比性前提满足。

### 4.3 `tasks_completed` 的判定

任务在 `_handle_completions` 被认定为完成的条件 ([engine.py](../simulation/engine.py))：

1. trace 已喂完（`tick_index ≥ T`）；
2. `duration_steps` 倒计时到 0；
3. `pending_traffic` 已排空（或被 timeout 断路器强清）。

只要触发 §2.7 的 timeout 强清，该任务的 `congestion_wait_ticks` 已累加、`pending_traffic` 被丢弃残量但仍正常完成 —— 这会拉低 throughput 不会拉低 completion_rate。

---

## 5. STPS 专属

### 5.1 `mean_start_offset` / `p95_start_offset`

```text
on STPS admit:   metrics.start_offsets.append(task.start_offset)
report mean:     numpy.mean(start_offsets)
report p95 :     numpy.percentile(start_offsets, 95)
```

- 代码：[util/metrics.py:156](../util/metrics.py#L156) → [util/metrics.py:406-417](../util/metrics.py#L406-L417)
- 单位 ticks，仅 STPS 系列 > 0；baseline 全 0。
- 是 Layer 1 intrinsic offset cost 的直接代理。

### 5.2 `reject_rate_bw`

```text
reject_rate_bw = bw_rejections / task_count
              = #{ task with reject_reason = "bw_max_exceeded" } / total
```

- 代码：[util/metrics.py:159-161](../util/metrics.py#L159-L161) → [util/metrics.py:419-423](../util/metrics.py#L419-L423)
- 在 [traffic_optim.md §5](traffic_optim.md) 改造后 STPS 不再 reject，该项实测 = 0。保留是为了与旧版兼容、并捕获其他可能的 reject 来源。

---

## 6. 三类指标的物理对应

把 §1 / §2 / §3 三组指标放到同一时空格里，看清楚它们各自衡量的是什么：

```text
                  空间维 (across cards, per-tick)        时间维 (within card, along T)         瞬态拥塞 (per-tick)
                  ------------------------------------   ------------------------------------  --------------------------------
served (实际)     card_cv, card_jfi, card_lif,           time_card_cv, time_card_jfi,          —
                  max_min_ratio, avg_load_imbalance      time_card_lif, time_card_max_min,
                  [§1]                                   time_card_load_variance [§1b]

demand (请求)     avg_demand_cv [§2.6]                   —                                     avg_congestion_ratio,
                                                                                              congested_card_tick_frac
                                                                                              [§2.1-2.2]

backlog (队列)    avg_backlog_cv [§2.6]                  —                                     peak_backlog [§2.3],
                                                                                              mean/p95_cong_wait_ticks (per-task)

util (= s/cap)    —                                      —                                     avg_utilization [§2.4]

delay (per-task)  —                                      —                                     avg/p50/p95/p99/max_delay [§3]
```

[Q0_result.md §1-§4](Q0_result.md) 用的列表里：

- **空间均衡列** (`card_cv / JFI / LIF / max_min_ratio`) 全部来自 §1 —— across cards, per-tick。
- **时间均衡列** (`time_card_cv / time_card_jfi / time_card_lif / time_card_max_min_ratio`) 来自 §1b —— within card, along T；STPS Stage B 的“主场”指标。
- **瞬态拥塞列** (`cong_ratio / cong_wait`) 来自 §2.1 / §2.5。
- **吞吐 / 延时列** 来自 §4 / §3。

四组互不相同维度，所以 “STPS §1 不赢 + §1b 赢 + §2 赢 + §4 略输” 这些反直觉现象都能在表格里同时出现，不矛盾 —— 它们度量的根本不是同一回事。

---

## 7. 复现 / 重算

```bash
/root/miniconda3/envs/snn/bin/python -c "
import csv, statistics
with open('data/q0/main_summary.csv') as f:
    for row in csv.DictReader(f):
        print(row['scheduler'], row['card_cv_mean'], row['avg_congestion_ratio_mean'])
"
```

per-seed raw 数据：[`data/q0/main_raw.csv`](../data/q0/main_raw.csv)、[`data/q0/arrival_raw.csv`](../data/q0/arrival_raw.csv)、[`data/q0/scale16_raw.csv`](../data/q0/scale16_raw.csv)。

per-tick snapshot 数据（含 demand/served/backlog/cong/util 6 列）：`data/q0/_raw/*_loads_*.csv`（由 [MetricsWriter.start_csv](../util/metrics.py#L484) 产生）。
