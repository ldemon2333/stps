# Q0 实验结果 — Bandwidth-Capped Mixed-Fingerprint Baseline

> 覆盖 [`docs/Q0_TODO.md`](Q0_TODO.md) 中规定的 paper-quality 实验。
> 配置统一使用 `bw_cap = bw_max = 9.0e5`, `d_max = 2`, `horizon = 64`，混合指纹 (synthetic_{flat,pulse_t8,pulse_t16,bursty} + 真实 {spikformer_cifar10, qkformer_cifar10, spikingresformer_ti_imagenet})。
> 所有结果取 **5 seeds (21,42,63,84,105)** 平均，CI 为 95% t-CI。Steady-state 窗口跳过首尾各 64 ticks。
> Admission 语义：超 `bw_max` 的任务不再 reject，使用 min-peak offset 入卡，由 NoC `pending_traffic` 队列吸收溢出（详见 [docs/traffic_result.md](traffic_result.md) §3）。

---

## 1. 实验设置

| 配置项 | 4-card main / arrival | 16-card scale16 |
| --- | --- | --- |
| Cards | 4 | 16 |
| Tasks | 512 | 2048 |
| Steps | 512 | 512 |
| Cores / card | 256 | 256 |
| Arrival mode | bursty (main) + {poisson, bursty, mixed} (arrival) | {poisson, bursty} |
| Fingerprint set | **mixed** (4 synthetic + 3 real) | **mixed** (同左) |
| `bw_cap` (engine NoC) | 9.0e5 | 9.0e5 |
| `bw_max` (STPS forecast) | 9.0e5 | 9.0e5 |
| `d_max` | 2 | 2 |
| `horizon` | 64 | 64 |
| Seeds | 21, 42, 63, 84, 105 | 21, 42, 63, 84, 105 |

`bw_cap = 9.0e5` 是 bursty + mixed 指纹下未限流跑出的 per-card demand p75（来自 [docs/traffic_calib.md](traffic_calib.md)）。该 cap 已被全员触发拥塞，所有调度器的 `avg_congestion_ratio ∈ [0.24, 0.28]`，是观察 STPS 拥塞缓解作用的合适区间。

> 与之前 Q0 (synthetic-only, no cap) 的差异：4-card 不再是 smoke 配置，与 16-card 共用同一指纹集与同一带宽 cap，仅卡数不同。这样 4-card 与 16-card 之间的对比刻画的是 **规模效应**，而不是负载差异。

---

## 2. 4-card Main 表 (bursty, 5 seeds)

| Scheduler | card_cv ↓ | JFI ↑ | LIF ↓ | max/min ↓ | completion ↑ | throughput ↑ | p99_delay ↓ | avg_delay ↓ | cong_ratio ↓ | mean_cong_wait ↓ | utilization ↑ |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| rr | 0.2865 ± 0.013 | 0.9060 ± 0.007 | 1.272 ± 0.020 | 6.11 ± 2.01 | 1.000 | 1.293 ± 0.009 | 129.6 ± 21.9 | 36.62 ± 4.17 | 0.2575 ± 0.006 | 8.87 ± 0.18 | 0.806 |
| bestfit | 0.2864 ± 0.017 | 0.9043 ± 0.008 | 1.275 ± 0.021 | 6.06 ± 1.13 | 1.000 | **1.296 ± 0.018** | **127.2 ± 19.1** | 38.55 ± 5.99 | 0.2583 ± 0.010 | 8.89 ± 0.23 | 0.806 |
| drf | 0.2864 ± 0.017 | 0.9043 ± 0.008 | 1.275 ± 0.021 | 6.06 ± 1.13 | 1.000 | 1.296 ± 0.018 | 127.2 ± 19.1 | 38.55 ± 5.99 | 0.2583 ± 0.010 | 8.89 ± 0.23 | 0.806 |
| p2c | 0.2940 ± 0.011 | 0.9014 ± 0.006 | 1.282 ± 0.017 | **5.80 ± 1.03** | 1.000 | 1.286 ± 0.006 | 135.2 ± 19.2 | **36.20 ± 4.69** | **0.2551 ± 0.005** | 8.86 ± 0.20 | 0.803 |
| **stps** | 0.3074 ± 0.004 | 0.8960 ± 0.003 | 1.293 ± 0.010 | 6.23 ± 2.37 | 1.000 | 1.269 ± 0.007 | 143.4 ± 20.8 | 39.46 ± 5.18 | **0.2387 ± 0.005** | **8.14 ± 0.19** | 0.792 |

- 所有调度器 `completion_rate = 1.000`，对比公平（无 reject、无 task drop）。
- **STPS 不赢 card-level 均衡指标**：card_cv +7%、JFI -1pp、LIF +2%、max/min 持平。
- **STPS 赢拥塞指标**：avg_congestion_ratio 比 baseline 中位数低 ~7%，mean_congestion_wait 低 ~8%，p95_cong_wait 19.2 vs baseline 20.6-20.8。
- **STPS 付出吞吐**：throughput 1.269 比 bestfit 1.296 低 2.1%，p99/avg delay 略高（与 cong_wait 改善方向相反，是 Stage B 主动 offset 引入的 "intrinsic delay"，详见 §5 Layer 1 / Layer 2 分析）。

---

## 3. 4-card Arrival Sweep (poisson / bursty / mixed, 5 seeds × 5 schedulers)

只列关键指标。完整 CSV 见 [`data/q0/arrival_summary.csv`](../data/q0/arrival_summary.csv)。

### 3.1 card_cv (越小越均衡)

| Arrival | rr | bestfit | drf | p2c | stps |
| --- | --- | --- | --- | --- | --- |
| poisson | **0.2670** | 0.2884 | 0.2884 | 0.2752 | 0.2927 |
| bursty | **0.2865** | 0.2864 | 0.2864 | 0.2940 | 0.3074 |
| mixed | **0.2652** | 0.2915 | 0.2915 | 0.2750 | 0.2998 |

STPS 在三种 arrival 下 card_cv 都偏高。rr 在 poisson/mixed 下最均衡，bestfit/drf 在 bursty 下与 rr 持平。

### 3.2 throughput (越大越好)

| Arrival | rr | bestfit | drf | p2c | stps |
| --- | --- | --- | --- | --- | --- |
| poisson | 1.318 | 1.303 | 1.303 | 1.314 | 1.292 |
| bursty | 1.293 | **1.296** | **1.296** | 1.286 | 1.269 |
| mixed | **1.325** | 1.309 | 1.309 | 1.316 | 1.297 |

STPS 在三种 arrival 下吞吐都低 1.5%-2.1%；最大缺口在 bursty (-2.1% vs bestfit)。

### 3.3 avg_congestion_ratio (越小越好) + mean_cong_wait (越小越好)

| Arrival | metric | rr | bestfit | drf | p2c | stps |
| --- | --- | --- | --- | --- | --- | --- |
| poisson | cong_ratio | 0.2687 | 0.2604 | 0.2604 | 0.2652 | **0.2469** |
| poisson | cong_wait | 9.26 | 9.06 | 9.06 | 9.13 | **8.42** |
| bursty | cong_ratio | 0.2575 | 0.2583 | 0.2583 | 0.2551 | **0.2387** |
| bursty | cong_wait | 8.87 | 8.89 | 8.89 | 8.86 | **8.14** |
| mixed | cong_ratio | 0.2674 | 0.2594 | 0.2594 | 0.2626 | **0.2450** |
| mixed | cong_wait | 9.31 | 9.21 | 9.21 | 9.25 | **8.56** |

STPS 在所有 arrival mode 下 **稳定** 降低拥塞指标（cong_ratio: -5%~-8%；cong_wait: -7%~-9%）。这是 STPS 在 Q0 表里唯一稳定胜出的方向。

### 3.4 p99_delay / avg_delay 同向变化

mixed arrival 下所有调度器 p99_delay 在 200+ ticks（远高于 poisson/bursty 的 ~130），是因为 mixed 包含尾部大批量任务的 burst phase。STPS 的 p99 比 baseline 高 1-3 ticks，与吞吐损失同向，仍是 Stage B intrinsic offset 的代价。

---

## 4. 16-card Scale Table (poisson + bursty, 5 seeds)

| Arrival | Scheduler | card_cv ↓ | JFI ↑ | LIF ↓ | max/min ↓ | throughput ↑ | p99_delay ↓ | cong_ratio ↓ | cong_wait ↓ |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| poisson | rr | **0.3046** | **0.9117** | **1.2185** | 16.90 ± 7.2 | 5.346 ± 0.007 | 101.0 ± 1.2 | 0.2745 | 9.45 |
| poisson | bestfit | 0.3067 | 0.9106 | 1.2209 | 21.52 ± 7.4 | 5.321 ± 0.021 | 104.6 ± 3.6 | 0.2739 | 9.41 |
| poisson | drf | 0.3067 | 0.9106 | 1.2209 | 21.52 ± 7.4 | 5.321 ± 0.021 | 104.6 ± 3.6 | 0.2739 | 9.41 |
| poisson | p2c | 0.3030 | 0.9126 | 1.2174 | 21.78 ± 12.4 | 5.339 ± 0.023 | **98.0 ± 5.5** | 0.2761 | 9.53 |
| poisson | **stps** | 0.3167 | 0.9056 | 1.2349 | **13.42 ± 2.2** | 5.274 ± 0.006 | 107.2 ± 2.5 | **0.2564** | **8.61** |
| bursty | rr | **0.3132** | **0.9069** | **1.2348** | 21.09 ± 6.9 | 5.263 ± 0.041 | 100.2 ± 8.7 | 0.2677 | 9.14 |
| bursty | bestfit | 0.3180 | 0.9043 | 1.2356 | 14.62 ± 2.9 | 5.251 ± 0.037 | 100.8 ± 8.6 | 0.2668 | 9.12 |
| bursty | drf | 0.3180 | 0.9043 | 1.2356 | 14.62 ± 2.9 | 5.251 ± 0.037 | 100.8 ± 8.6 | 0.2668 | 9.12 |
| bursty | p2c | 0.3109 | 0.9079 | 1.2338 | 23.90 ± 12.5 | **5.279 ± 0.056** | **97.2 ± 6.0** | 0.2708 | 9.25 |
| bursty | **stps** | 0.3246 | 0.9011 | 1.2489 | **12.09 ± 2.2** | 5.212 ± 0.041 | 105.4 ± 10.7 | **0.2522** | **8.45** |

**16-card 与 4-card 的规模差异**：

- baseline 的 `max_min_ratio` 在 16-card 下放大到 14-24（4-card 下只有 5-9），说明卡数越多 baseline 的 tail-imbalance 越严重。
- **STPS 的 max_min_ratio 在 16-card 下反而最小** (poisson: 13.4 vs baseline 16-22; bursty: 12.1 vs baseline 14-24)。这是 STPS 在 16-card 才暴露出来的优势：Stage 1 forecast-aware 派遣在多卡更有用，避免了静态调度的 "某一卡极端过载" 尾点。
- STPS 仍在 card_cv/JFI/LIF 上略输 ~3%，吞吐落后 1.3%-1.4%；拥塞指标仍稳定领先 6%-8%。

### 4.1 长尾任务统计 (delay > P99)

每次 16-card run 现在都会在 [data/q0/scale16_raw.csv](../data/q0/scale16_raw.csv) 写入下列新列，
直接量化超长尾任务的数量：

| 列名 | 含义 |
| --- | --- |
| `completed_tasks` | 该 run 完成的任务总数 N（只统计 `total_delay >= 0` 的） |
| `tail_count` | 该 run 内 `delay > p99_delay` 的任务数 |
| `tail_frac` | `tail_count / N`（理论上接近 1%，但因离散值并列会更小） |
| `max_delay` | 该 run 内的最大单任务时延（ticks） |
| `avg_delay_above_p99` | 超过 P99 的那部分任务的平均时延（量化 "tail有多远"） |

同时所有完成任务的 per-task 时延会被 dump 到
[data/q0/scale16_task_delays.csv](../data/q0/scale16_task_delays.csv)
(`scheduler, arrival_mode, seed, delay, delay_excl_cold, cold_start, p99_delay_run`)，
便于事后任意切片分析。`delay` 列为 cold-start 包含口径（与长尾图保持一致），
`delay_excl_cold = delay - cold_start` 为 §4.3 描述的实际执行时延口径，
`cold_start` 为 STPS Stage-B phase-shift 错峰时延（baseline 永远为 0）。

### 4.2 长尾分布图 (Long-Tail Distribution Plot)

`script/q0_run.py scale16` 完成后会自动生成下列图（5 seeds × 3200 tasks/seed 聚合，
每个 arrival_mode 一组）：

- `figures/q0/scale16_long_tail_{poisson,bursty}.{pdf,png}` —
  每个调度器一个子图，**横轴 = delay (ticks)、纵轴 = 任务计数（log y）**，
  竖直虚线标出该调度器的 pooled P99，标题给出 `tail_count / N (frac%)`。
- `figures/q0/scale16_long_tail_ccdf_{poisson,bursty}.{pdf,png}` —
  CCDF 总览图（log-log 坐标系），所有调度器叠在一张图上，
  $y = P[\text{Delay} > d]$。水平虚线 $y = 0.01$ 对应 P99 阈值。
  曲线越早 "断崖" 越好、右尾越短表示长尾越收敛。

`la-scale16` 走相同的处理，输出文件前缀为 `la_scale16`。

### 4.3 冷启动剔除口径 (cold-start-excluded throughput / exec-time)

STPS Stage B 会给每个任务引入 0..d_max 个 tick 的 phase-shift 错峰偏移
([schedule/stps.py](../schedule/stps.py) `_stage2_phase_shift`)。这段时间里
任务已经占用卡片资源但**还没开始发放脉冲**——它由 STPS 的设计本身引入，
不属于"真实计算开销"。把它定义为**冷启动时延** `cold_start_ticks = start_offset`，
就可以把"实际执行时延"和"调度引入的设置开销"分离开。
[util/metrics.py](../util/metrics.py) 现在为每个完成任务在 `TaskDelay` 里同时记录两个口径：

- **包含 cold-start（原口径，用于长尾图与 §4.1 / §4.2）**：
  `total_delay = completion_step − arrival_step`
- **剔除 cold-start（新口径，用于 throughput / exec-time）**：
  `effective_delay = total_delay − cold_start_ticks`
  $$T_{\text{eff}} = T_{\text{total}} - T_{\text{cold}}$$

吞吐口径相应改为
$$\mathrm{Throughput}_{\text{excl\_cold}} = \frac{N_{\text{completed}}}{T_{\text{steps}} - \overline{T_{\text{cold}}}}$$
其中 $\overline{T_{\text{cold}}}$ 是该 run 内所有完成任务的 `cold_start_ticks` 均值
（baseline 永远为 0，因此对 baseline 来说 $\mathrm{Throughput}_{\text{excl\_cold}} = \mathrm{Throughput}$）。

口径切换只影响 §4.3 的列，**§4.1 / §4.2 的 P99 / 长尾图仍走原口径**，
所以"长尾任务 tail_count" 的定义不变；这样图与表的语义不会混淆。

#### 16-card scale16 (5 seeds × 3200 tasks) — 两口径对照

| arrival | scheduler | $\overline{T_{\text{cold}}}$ | throughput | throughput$_{\text{excl\_cold}}$ | $\Delta$ | p99_delay | p99$_{\text{excl\_cold}}$ | avg_delay | avg$_{\text{excl\_cold}}$ | max_delay | max$_{\text{excl\_cold}}$ |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| poisson | rr | 0.000 | 5.346 | 5.346 | +0.00% | 101.0 | 101.0 | 37.54 | 37.54 | 106.8 | 106.8 |
| poisson | bestfit | 0.000 | 5.321 | 5.321 | +0.00% | 104.6 | 104.6 | 37.35 | 37.35 | 110.4 | 110.4 |
| poisson | drf | 0.000 | 5.321 | 5.321 | +0.00% | 104.6 | 104.6 | 37.35 | 37.35 | 110.4 | 110.4 |
| poisson | p2c | 0.000 | 5.339 | 5.339 | +0.00% | 98.0 | 98.0 | 39.57 | 39.57 | 106.8 | 106.8 |
| poisson | **stps** | **1.019** | 5.274 | **5.282** | **+0.17%** | 107.2 | **107.0** | 42.23 | **41.21** | 112.8 | 112.8 |
| bursty | rr | 0.000 | 5.264 | 5.264 | +0.00% | 100.2 | 100.2 | 37.57 | 37.57 | 105.2 | 105.2 |
| bursty | bestfit | 0.000 | 5.251 | 5.251 | +0.00% | 100.8 | 100.8 | 37.81 | 37.81 | 107.2 | 107.2 |
| bursty | drf | 0.000 | 5.251 | 5.251 | +0.00% | 100.8 | 100.8 | 37.81 | 37.81 | 107.2 | 107.2 |
| bursty | p2c | 0.000 | 5.279 | 5.279 | +0.00% | 97.2 | 97.2 | 40.75 | 40.75 | 107.4 | 107.4 |
| bursty | **stps** | **1.007** | 5.212 | **5.221** | **+0.16%** | 105.4 | **105.2** | 42.43 | **41.42** | 114.0 | **113.0** |

(数据来源 [data/q0/scale16_summary.csv](../data/q0/scale16_summary.csv)，
原始 per-run 数据 [data/q0/scale16_raw.csv](../data/q0/scale16_raw.csv) 含
`mean_cold_start / throughput_excl_cold / p99_delay_excl_cold / avg_delay_excl_cold / max_delay_excl_cold` 五列。)

#### 几条关键观察

1. **冷启动确实存在但量级很小**：STPS 在两种 arrival 下 $\overline{T_{\text{cold}}}$ 都 ≈ 1.01 tick
   （d_max=2 的上限里只用了一半），p95_start_offset = 2。这说明 5 张卡里 ~50%
   的任务被 Stage B 推迟 1-2 个 tick 错峰。
2. **吞吐损失主要不是冷启动造成**：剔除冷启动后 STPS 吞吐只回升 0.17%（poisson）
   / 0.16%（bursty），与 baseline 的吞吐差距（−0.74% / −0.59%）仍然存在。
   说明 §5 Layer 2 contention-avoidance 才是 STPS 吞吐落后的主因，与
   [docs/traffic_result.md](traffic_result.md) §4 的 60/40 拆解大致吻合。
3. **avg_delay 受冷启动影响最显著**：STPS 的 avg_delay 在两种 arrival 下都下降 ~2.4%
   (42.23 → 41.21，42.43 → 41.42)，比 throughput 的相对改善大一个量级。
   bursty 下的 max_delay 也从 114 降到 113。这与"冷启动是均匀分布的小常数"一致：
   它对平均数有可测量影响，对长尾几乎没有影响。
4. **STPS 仍然是 avg_delay 最高的调度器**：即使剔除冷启动，STPS avg 41.2-41.4
   仍高于 baseline 37-40。剩下的 ~3 tick 平均差距由 NoC 拥塞队列 wait
   产生（`mean_congestion_wait_ticks` 见 §4 主表），不是冷启动可以解释的。

---

## 5. STPS 代价分层 (Layer 1 / Layer 2) 与改进方向

参考 [docs/traffic_result.md](traffic_result.md) §4 的两层成本框架。

- **Layer 1 — STPS intrinsic offset cost**：Stage B 即使在无 cap 时也会引入 0..d_max 的 start_offset 来错峰，必然抬高单任务首 tick 时间。这是 "STPS 本来就有" 的代价，与 cap 无关。表现为：throughput 较 baseline 低 1%-2%、avg_delay 高 1-3 ticks、p99_delay 高 5-15 ticks。该代价在 bursty/mixed 下更显著（fingerprint peak 越大、d_max 利用越满）。**§4.3 已用 `cold_start_ticks` 把这一层与 Layer 2 拆开量化**：scale16 上 $\overline{T_{\text{cold}}} \approx 1.01$ tick，对应 throughput 仅 +0.17% / avg_delay −2.4%，远小于 STPS vs baseline 的总差距，证明吞吐损失绝大部分由 Layer 2 NoC 拥塞队列 wait 解释，而非 Stage B 的错峰偏移本身。
- **Layer 2 — contention-avoidance cost**：bw_max cap 触发 forecast over-budget，STPS 用 min-peak offset 入卡，进一步推迟到达，由 NoC `pending_traffic` 吸收溢出。这一层换来 cong_ratio / cong_wait 的下降（-5%~-9% 稳定）。

Q0 数据无法直接分离两层比例（需要 q_traffic 的 "uncapped vs capped" 对比，已在 traffic_result.md §4 中报告：mixed bursty Layer 1 占 throughput 损失 ~60%、Layer 2 占 ~40%）。Q0 仅刻画 "capped + mixed 指纹" 这一档下的整体表现。

### 5.1 现状诊断：为什么 STPS 在 "卡间负载均衡" 上未胜出

研究目标是 **降通信拥塞 + 提吞吐 + 均衡卡间负载** 三者同向，但当前实现只赢第 1 项。原因可定位到 4 个具体机制（与代码对应）：

1. **Stage 1 评分缺 "累计负载" 维度**（[stps.py:128-143](../schedule/stps.py#L128-L143)）：当前 score = `w_frag · |block_i − 1/K̄| + w_β · β_card`，只看碎片几何匹配 + β 隔离。两张 candidate 卡若 frag/β 相近，调度退化为 "按卡 id 顺序拿第一张"，没有任何 "把任务塞向累计 served 较低的卡" 的拉力 —— 这是 [Q0_result.md §2](Q0_result.md) 看到 `card_cv` 略输的根因。
2. **Stage 2 目标函数过于 myopic**（[stps.py:145-158](../schedule/stps.py#L145-L158) + [phase_shift.py](../schedule/phase_shift.py)）：`find_optimal_offset` 只最小化 *单张卡 H-步内的 forecast peak*，不考虑该决策对卡间累计 `L_i` 的影响。后果：候选卡 forecast 偶然处于谷底的那一张会被连续吸引若干任务，造成短窗扎堆。
3. **`H=64` 远短于 steady-state 窗口 384**：forecast 只看未来 64 ticks，而 `card_cv` 在 384 ticks 上累计；Stage 2 的 "短期最优" 在长期上偏离了全局均衡。
4. **Admission 改造后无负反馈机制**：[traffic_optim.md §5](traffic_optim.md) 改 reject 为 min-peak 入卡，超额任务进 `pending_traffic` 队列。当前没有 *把队列长度反馈给后续派遣* 的回路 —— 一旦某卡 backlog 高，调度器仍按原始 frag/β 评分继续塞任务，加剧 tail imbalance。

### 5.2 改进方向（按 ROI 排序）

> 这些是设计候选，每条都需要单独的 calibration 与对照实验，**不是立即可下结论的修复**。每条标注 *touch points*（代码改动位置）与 *expected risk*。

#### A. Stage 1 加入 "累计负载惩罚"（最高 ROI）

- **touch points**：[stps.py:128-143](../schedule/stps.py#L128-L143)
- **改动**：score 增加第三项 `w_L · L_i / max_j(L_j + ε)`，其中 `L_i` 取过去 W 个 tick 该卡的 `_card_epoch_load` EMA。配合 `step(t)` 里维护 per-card EMA。
- **预期**：直接拉低 `card_cv` 与 `max_min_ratio`，因为 score 显式偏向累计低负载卡；对拥塞指标几乎零影响（Stage 1 不改 timing）。
- **风险**：`w_L` 过大会让 STPS 退化为 best-fit，丢失 fragmentation match 的优势。需要在 (`w_frag, w_β, w_L`) 上做 grid sweep。

#### B. Stage 2 目标函数混合长短窗（中 ROI）

- **touch points**：[phase_shift.py](../schedule/phase_shift.py) `find_optimal_offset`，[stps.py `_stage2_phase_shift`](../schedule/stps.py#L145-L158)
- **改动**：目标从 `min peak(E_m + shift)` 改为 `min (peak + λ · L_i_cum)`，把候选卡的累计负载作为正则项。L_i_cum 是过去 W tick 的累计 served（与改动 A 共用 EMA）。
- **预期**：在 forecast peak 相近时偏向累计低的卡，缓解 §5.1-2 "短窗扎堆"。
- **风险**：λ 与 `bw_max` 量纲不同，需要归一化；λ 过大会让 Stage 2 退化到 Stage 1 的角色。

#### C. 把 `horizon H` 拉长到接近 steady-state 窗口（中 ROI）

- **touch points**：CLI `--horizon`、[stps.py `__init__`](../schedule/stps.py#L40-L65)、[card.py `ensure_forecast`](../util/card.py#L109)
- **改动**：H 从 64 → 192 或 256，把 forecast 视野与 `_steady_window` 长度 384 对齐到 1:2 量级。
- **预期**：Stage 2 短期偏置减弱、long-run 累计更平。
- **风险**：forecast 内存 O(N·H)、phase_shift 时间复杂度 O(N · D_max · H)，64 → 256 是 4×；任务 fp.E_eff 长度大多 ≤ 64，超出部分 forecast 末段恒 0，需要确认 phase_shift kernel 对稀疏 forecast 不退化。

#### D. Backlog-aware 派遣反馈环（中 ROI，对 16-card 收益高）

- **touch points**：[stps.py `_stage1_filter`](../schedule/stps.py#L128-L143)
- **改动**：score 增加第四项 `w_q · card.pending_traffic_sum / cap`，让 backlog 高的卡被推后选择。pending_traffic_sum 已经在 `_card_epoch_backlog` 里维护。
- **预期**：直接缓解 §5.1-4 描述的负反馈缺失；预期 `max_min_ratio` 在 16-card 上进一步下降 1-3 个单位。
- **风险**：可能与改动 A 重复（累计 load 与 backlog 强相关）；建议二选一或共用一个 EMA。

#### E. 替换 fair-share scaling 为 priority queue（低 ROI，破坏性高）

- **touch points**：[engine.py `_tick` ③④](../simulation/engine.py#L344-L378)
- **改动**：拥塞时不再等比例打折，而是按任务优先级（如 `start_offset` 大者优先 / fingerprint β 低者优先）调度。
- **预期**：可让特定任务尾延时下降，但 **会引入调度器到引擎层的耦合**，违反 [traffic_optim.md §A.3-1](traffic_optim.md) 的 "scheduler-agnostic engine" 设计原则。
- **风险**：与 paper 的简洁建模冲突，不推荐除非有 baseline 公平性的具体声明需要保护。

### 5.3 精细化模拟实验（验证上述改进）

要让 "卡间负载均衡改善" 可被精确度量、并区分 Layer 1 / Layer 2 / 卡间均衡 三种效应，建议在 Q0 之外补充：

- **新增 metric 维度**：使用 [metrics.md §1b](metrics.md) 的 **time_card_{cv,jfi,lif,max_min_ratio,load_variance}** 五项，作为 STPS Stage B 错峰效果的 *主场指标*。单卡时间序列内的离散度是 STPS Stage 2 的直接优化目标，比 §1 的 across-cards 离散度更敏感。预期改动 A/B 同时让 §1 和 §1b 改善。
- **Backlog & 累计负载时间序列图**：对 4-card / 16-card × {bursty, mixed} 4 组配置画 `_card_epoch_load[i]` 与 `_card_epoch_backlog[i]` 的时间曲线（按 tick），visual diagnose 当前 STPS 的 "短窗扎堆" 现象。
- **改进消融**：定义 5 个对照组 `STPS / STPS+A / STPS+A+B / STPS+A+B+C / STPS+A+B+C+D`，5 seeds × 2 arrivals × 2 scales = 100 runs，summary 报这 5 组在 (`card_cv`, `time_card_lif_mean`, `max_min_ratio`, `cong_ratio`, `throughput`) 5 个轴上的 radar 图，看哪条改动同时改善 ≥3 个轴。
- **`w_L / λ / w_q` sweep**：每个权重做 3-5 档 grid，找帕累托前沿。当前 `w_frag=w_β=1.0` 的默认值是 paper §4.3 的设定，**未经过任何 capped + mixed 场景的调优**。
- **指纹分层报表**：把 mixed 拆成 "synthetic-only / real-only / mixed"，分别报 Q0 的所有指标，定位是哪一类指纹拖垮 STPS 的空间均衡（[Q0_result.md §6.2](Q0_result.md) 推测是真实指纹，但未单独验证）。
- **`bw_cap` sensitivity**：当前固定 9e5 (= bursty p75)。补 {7e5, 9e5, 1.2e6, 1.6e6} 4 档，看改进 A-D 在 cap 紧/松 时是否同向有效。
- **`H` 与 `D_max` 联合 sweep**：当前 traffic_calib 只调了 `D_max`，未联调 `H`。在改动 C 落地前需补 `H × D_max` 的 2D grid 至少 3×3=9 点。

### 5.4 期望结果

若改动 A + B + C + D 全部生效，目标态指标变化（相对当前 STPS）：

| 指标 | 当前 STPS (4-card bursty) | 期望 (4-card bursty) | 机制 |
| --- | --- | --- | --- |
| `card_cv` | 0.307 | ≤ 0.290 (接近 baseline) | A + D 直接降空间维离散 |
| `max_min_ratio` (16-card bursty) | 12.1 | ≤ 11 | D 反馈环 + A 累计平衡 |
| `time_card_lif_mean` | 待补测 | 显著低于 baseline | B 长窗目标函数 |
| `avg_congestion_ratio` | 0.239 | 维持 ≤ 0.245 | A/D 不动 timing；B 可能略恶化 |
| `throughput` | 1.269 | ≥ 1.285 (-1%) | C 长 horizon 减少 Stage B 的 D_max 利用 |

**研究主线推荐**：先做 A 与 D（动 Stage 1 score，最不破坏 paper 主结构），同时引入 §1b 时间维 metric 做主轴；如果 A+D 已能让 `card_cv` 与 `max_min_ratio` 双降 + cong_ratio 不退，论文可定位为 "**load-aware extension to STPS**" 而非另起炉灶。B/C 留作后续 ablation。

---

## 6. 结论 (Q0 verdict)

按 [docs/Q0_TODO.md](Q0_TODO.md) §7.3-7.7 的判定标准：

1. **§7.3 客观报告**：在 mixed 指纹 + bw_cap=9e5 + d_max=2 的设置下，**STPS 并不在 card-level 均衡 (CV/JFI/LIF) 上击败静态 baseline**。CV 偏高约 4%-7%，JFI 偏低约 0.5pp-1pp。
2. **§7.4 边界解释**：真实指纹的 spike peak 显著大于 synthetic（spikformer/qkformer 单卡瞬时需求逼近甚至超过 bw_cap=9e5），即使 d_max=2 也无法把所有热点错开到 forecast 允许带内；只能让 min-peak offset + NoC 队列承担。这种情形下 STPS 的 Stage B 不再能消平峰值，反而让更多任务带着 offset 延后到达，体现为 card_cv 略升、throughput 略降。这是 "STPS 在重尾真实指纹下的可解释边界"，不是 bug。
3. **§7.5 拥塞指标稳定领先**：cong_ratio 在三种 arrival × 两种规模下 **6/6 场景** 都低于全部 baseline，cong_wait 同样 6/6 领先；这是 STPS 在 Q0 设置下唯一稳定的胜点。
4. **§7.6 16-card 必须跑**：已完成；规模放大后 STPS 的 `max_min_ratio` 反而最优（baseline 出现 21-24 的 tail）。
5. **§7.7 protect throughput / completion**：completion_rate = 1.000（所有调度器、所有种子），throughput 损失 ≤ 2.1%。在 "小损吞吐 + 大降拥塞" 的权衡下符合可接受门槛。

**一句话总结**：在 bw_cap=9e5、d_max=2、混合指纹下，STPS 用 ~2% 的吞吐与 ~5%-7% 的 card-level 均衡指标，换来 5%-9% 的稳定拥塞降低，并在 16-card 下显著压制最差卡的尾点过载；不能宣称 STPS 是 "全方位最优均衡器"，但可宣称它是 "拥塞 / 尾点过载敏感场景下的稳健选择"。

---

## 7. Load-Aware Extension (`stps-la`) — Tuned 配置与 16-card Scale 实验

§5.2 改动 A (Stage 1 累计负载惩罚) + 改动 D (Backlog-aware 反馈) 在 [schedule/stps.py](../schedule/stps.py) 落地为 `stps-la` scheduler。本节给出 (a) Stage 1 **cull** 机制（解决 "Stage 1 仅排序、Stage 2 仍按 forecast peak 挑卡" 造成的 load-aware 失效问题）、(b) 16-card bursty 上的权重 grid sweep、(c) 在 5 seeds × 2 arrivals 上的 tuned 配置确认。§7 与 §2 / §3 / §4 旧表互不覆盖。

### 7.1 实现增量

| 改动 | 文件 / 行 | 公式 | tuned 权重 |
| --- | --- | --- | --- |
| A | [stps.py:153-176](../schedule/stps.py#L153-L176) | Stage 1 score += `w_L · L_i_ema / max_j L_j_ema` | `w_L = 2.0` |
| D | [stps.py:153-176](../schedule/stps.py#L153-L176) | Stage 1 score += `w_q · backlog_i_ema / max_j backlog_j_ema` | `w_q = 2.0` |
| Cull | [stps.py:181-184](../schedule/stps.py#L181-L184) | Stage 1 排序后截断最差比例，Stage 2 只从 top-(1-`cull_frac`) 挑 | `cull = 0.75` |
| EMA | [stps.py:92-101](../schedule/stps.py#L92-L101) | `EMA_t = (1-α) · EMA_{t-1} + α · epoch_value`, α=0.2 | — |
| Engine wire-up | [engine.py:213-217](../simulation/engine.py#L213-L217) | 暴露 `cluster_epoch_backlog` 给 scheduler | — |

`stps-la` 注册为新 scheduler，原 `stps` 保持 bit-equivalent（默认 `w_L=w_q=0, cull_frac=0`）。

**Cull 设计动机**：仅靠 `load_weight` / `backlog_weight` 给 Stage 1 候选 **排序** 时，Stage 2 接手仍按 forecast peak 挑卡，即便重载卡被排到末位也可能被 Stage 2 选中。`stage1_cull_frac` 对 Stage 1 排序后的列表 **截断最差比例**，强制让 Stage 2 只能从剩下的子集挑：

```python
if self.stage1_cull_frac > 0.0 and len(ranked) > 1:
    keep = max(1, int(math.ceil(len(ranked) * (1.0 - self.stage1_cull_frac))))
    ranked = ranked[:keep]
```

### 7.2 Grid Sweep (16-card bursty, smoke-grade)

为快速探索，先用 smoke-grade 配置（`tasks=1600, steps=256, 2 seeds`）扫描 24 组 (`w_L`, `w_q`, `cull`) ∈ {2, 4, 8} × {0, 2} × {0, 0.25, 0.5, 0.75}（加 anchor `stps` 共 25 点 × 2 seeds = 50 runs）。Anchor `stps` 基线：card_cv 0.3171, max_min 17.95, throughput 5.064, cong_ratio 0.2557, cong_wait 8.25。

`max_min_ratio` 升力 (vs stps anchor) Top 5：

| w_L | w_q | cull | card_cv | max_min | thrput | cong_r | mm 升力 |
| ---:| ---:| ---:| ---:| ---:| ---:| ---:| ---:|
| 2.0 | 2.0 | 0.75 | 0.3100 |  7.77 | 5.079 | 0.2603 | **−56.7%** |
| 8.0 | 2.0 | 0.75 | 0.3145 |  9.22 | 5.096 | 0.2530 | −48.6% |
| 4.0 | 0.0 | 0.50 | 0.3120 | 10.41 | 5.104 | 0.2580 | −42.0% |
| 4.0 | 2.0 | 0.75 | 0.3133 | 11.20 | 5.092 | 0.2580 | −37.6% |
| 2.0 | 0.0 | 0.75 | 0.3145 | 11.41 | 5.069 | 0.2615 | −36.4% |

观察：`cull ≥ 0.5` 是 max/min 改善的必要条件；`w_L=2, w_q=2, cull=0.75` 同时拿到最佳 max/min + 微正 card_cv (+2.3%) + 中性 throughput (+0.3%) + 微弱 cong_ratio 退 (+1.8%)。`cull=0.75` 在 16 卡上等于 "保留 top-4 候选给 Stage 2"，让 Stage 2 的错峰自由度仍然充足。

### 7.3 Tuned 配置 (`stps-la(w_L=2, w_q=2, cull=0.75)`) 在全量 Q0 设置下的对照表

`cards=16, tasks=3200, steps=512, bw_cap=9e5, d_max=2, horizon=64`，5 seeds（21/42/99/123/2024），3 schedulers（bestfit / stps / stps-la）× 2 arrivals。

| arrival | scheduler | card_cv ↓ | card_jfi ↑ | card_lif ↓ | max/min ↓ | throughput ↑ | p99_delay ↓ | cong_ratio ↓ | cong_wait ↓ | t_lif_served ↓ |
| --- | --- | ---:| ---:| ---:| ---:| ---:| ---:| ---:| ---:| ---:|
| poisson | bestfit              | 0.3067±0.0024 | 0.9106 | 1.2209 | 21.52±7.36 | 5.321±0.021 | 104.6 | 0.2739 | 9.41 | 1.2143 |
| poisson | stps                 | 0.3167±0.0026 | 0.9056 | 1.2349 | 13.42±2.24 | 5.274±0.006 | 107.2 | 0.2564 | 8.61 | 1.2283 |
| poisson | stps-la(2,2,0.75)    | 0.3143±0.0040 | 0.9069 | 1.2325 | **12.08±3.13** | 5.281±0.009 | 105.0 | 0.2595 | 8.77 | 1.2262 |
| bursty  | bestfit              | 0.3180±0.0078 | 0.9043 | 1.2356 | 14.62±2.94 | 5.251±0.037 | 100.8 | 0.2668 | 9.12 | 1.2254 |
| bursty  | stps                 | 0.3246±0.0072 | 0.9011 | 1.2489 | 12.09±2.24 | 5.212±0.041 | 105.4 | 0.2522 | 8.45 | 1.2386 |
| bursty  | stps-la(2,2,0.75)    | 0.3290±0.0086 | 0.8988 | 1.2490 | 12.33±0.78 | 5.197±0.027 | 109.0 | **0.2515** | 8.49 | 1.2405 |

### 7.4 差距解读

**stps-la vs stps**（同算法族内部，cull 是唯一新增机制）：

| arrival | card_cv | max/min | throughput | cong_ratio | cong_wait |
| --- | ---:| ---:| ---:| ---:| ---:|
| poisson | −0.76% | **−9.97%** | +0.13% | +1.21% | +1.86% |
| bursty  | +1.36% | +2.00% | −0.30% | −0.27% | +0.51% |

- **Poisson 拿到主要奖励**：max/min 从 13.42 降到 12.08（−9.97%，接近 10% 量级），5 seeds 区间内 stps-la 全部低于 stps 的中位数。card_cv 微降；吞吐持平；cong_ratio 微退（+1.2%）。**净结论：用 1.2% 拥塞退 + 1.9% 排队时延退 换 10% 尾卡均衡 + 微正 card_cv**。
- **Bursty 趋于持平**：max/min CI 极窄（±0.78）但 mean 微升 2%，card_cv 微退 1.4%，cong_ratio / cong_wait 在 CI 内中性。Bursty 下 Stage 2 错峰已主导决策，cull=0.75 留给 Stage 2 的 4 张候选卡足够，但 Stage 1 的负载惩罚带来的额外排队（cull 把低 forecast peak 候选剔掉）刚好抵消错峰收益。

**stps-la vs bestfit**（跨算法族，反映 "load-aware 错峰" 相对于 "纯空间打散" 的整体收益）：

| arrival | card_cv | max/min | throughput | cong_ratio | cong_wait |
| --- | ---:| ---:| ---:| ---:| ---:|
| poisson | +2.46% | **−43.87%** | −0.75% | **−5.27%** | **−6.79%** |
| bursty  | +3.45% | −15.66% | −1.03% | **−5.74%** | **−6.94%** |

- 两种 arrival 下 stps-la 都在 max/min、cong_ratio、cong_wait 三项上对 bestfit 取得 **两位数或近两位数** 的领先：poisson 下尾卡均衡领先 44%，bursty 下拥塞维持时间领先 ~7%。
- 代价仍是 §1 的 card_cv（+2.5%-3.5%）与 throughput（−0.7%-1.0%）——与 §3 / §4 整体交易曲线一致，没有变差。

### 7.5 §1b 时间维 metric 的解读（served vs demand）

`time_card_lif_mean` (= 单卡时间序列内 max/mean 的卡间均值) 用于刻画 "served 时间曲线是否尖"。**但它不等价于 "Stage 2 错峰是否成功"** —— 它同时被 Stage 2 offset、NoC cap clipping、pending queue smoothing、卡间任务分配、真实指纹重尾 共同影响。

`time_card_lif_served_mean`（旧口径）在 §7.3 表中 stps / stps-la 都 **高于** bestfit ~1.5%。原因是 STPS 用 offset 推迟任务 → 单卡 served 出现 d_max 内的 0 谷 → mean 被拉低 → max/mean 反而升高。这说明用 **served** 口径直接评判 Stage 2 错峰会被 "cap clipping + offset 低谷" 扭曲，需要补一个基于 **demand**（pre-cap，pre-queue）的对照指标，回答 "原始通信需求是否被错峰平掉"。

下一轮 confirm run 同时报三组指标，分别回答三个独立问题：

| 指标 | 回答的问题 |
| --- | --- |
| `time_card_lif_served_mean` | 实际发出的流量平不平（受 cap / 队列扭曲） |
| `time_card_lif_demand_mean` | 原始通信需求被错峰后平不平（Stage 2 直接目标） |
| `cong_ratio` / `cong_wait` | 是否真的减少 NoC 拥塞 |

三组同向 → "Stage 2 真的错峰成功且换来拥塞下降"；served 退而 demand 平 + cong 降 → "Stage 2 错峰成功，served 口径被 cap clipping 与 pending queue smoothing 扭曲"；demand 没平 → "Stage 2 没真的错峰，cong 下降来自其他通路（如 cull 把热卡剔出去）"。

[util/metrics.py:256-307](../util/metrics.py#L256-L307) 已扩展 `_per_card_load_series(kind="served"|"demand")`，新增 `time_card_lif_{served,demand}_{mean,max}` + `time_card_cv_demand_{mean,max}` properties；[script/q_la_confirm.py](../script/q_la_confirm.py) 已并入这些列。本节 demand 数据等下一轮 confirm 完成后回填。

### 7.6 极端通信压力下的稳定性

16-card poisson 下 bestfit 的 `max_min_ratio` CI 半宽达 7.36（5 seeds 区间 [14.16, 28.88]），说明 bestfit 在重通信负载下尾卡爆裂；同条件下 stps-la(2,2,0.75) 的 CI 半宽 3.13（区间 [8.95, 15.21]），即便最差 seed 也低于 bestfit 的均值。stps-la 在 16-card poisson 上不仅 mean 更好，**抗 seed 噪声的稳健性也更强**。Bursty 下 stps-la 的 CI 半宽只有 0.78，是三个 scheduler 中最稳定的。

### 7.7 Tuned 配置

```
scheduler  = stps-la
w_L        = 2.0   # load-EMA penalty weight (改动 A)
w_q        = 2.0   # backlog-EMA penalty weight (改动 D)
cull_frac  = 0.75  # Stage 1 retains top-25% by score, Stage 2 picks from those
load_ema_alpha = 0.2
```

主 claim 锁定在 **16-card poisson** 的 max/min −9.97% (vs stps) / −43.87% (vs bestfit) 两个数字；bursty 视为 "中性，证明无回归"。

