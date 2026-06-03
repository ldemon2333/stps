# STPS 算法过程 — 当前实现版本

> 关联代码：[schedule/stps.py](../schedule/stps.py)、[schedule/phase_shift.py](../schedule/phase_shift.py)、[schedule/hotspot_split.py](../schedule/hotspot_split.py)、[util/card.py](../util/card.py)、[simulation/engine.py](../simulation/engine.py)。
> 关联结果：[Q0_result.md](Q0_result.md)、[traffic_result.md](traffic_result.md)。
> 本文档刻画 [`docs/traffic_result.md`](traffic_result.md) §3 admission 语义改造后、`d_max` 调谐 ([traffic_result.md](traffic_result.md) §5) 之后的 **当前 STPS 算法过程**。与 paper §4.3 的差异集中在 admission contract（不再 reject 超额任务）。

---

## §A. 通信拥塞的引擎层建模 (engine-side, scheduler-agnostic)

> **本节定位**：刻画 [simulation/engine.py `_tick`](../simulation/engine.py#L309-L378) 这一层对 NoC 带宽冲突的物理建模。**所有调度器（rr / bestfit / drf / p2c / stps / 任意 baseline）跑的都是同一份 `_tick`**；调度器只决定 (i) 任务被放到哪张卡、(ii) 任务的 `start_offset` 是否非零。一旦任务落卡，拥塞建模与调度器解耦。把这一节放最前面是为了让后续 §1-§9 谈到的 "STPS 把超额流量交给 NoC 队列吸收" 有一个独立、可单测的底座。

### A.1 建模抽象与设定

当前模拟器采用的是 **per-card injection bandwidth cap + per-task single-slot backlog queue** 的拥塞模型：每张卡在每个 tick 只有一个总注入带宽上限 `bw_cap`；同一卡上所有任务的通信需求先相加，如果超过 cap，就按比例公平缩放服务量，未服务完的残量写回任务自己的 `pending_traffic`，下一 tick 优先继续发送。

这不是链路级 NoC 拓扑模型。模拟器不显式建 mesh / router / VC / hop / flit，也不追踪 source-destination pair；它把 NoC contention 抽象成“某张卡在某个 tick 的总注入需求超过可服务上限”。因此它适合比较调度策略是否能降低卡级通信峰值和排队等待，但不能直接回答“哪条链路堵了、路径长度如何影响 latency、路由策略是否更优”这类问题。

| 量 | 物理意义 | 来源 |
| --- | --- | --- |
| `bw_cap` | engine 层真实硬上限：每张卡每 tick 最多服务多少通信量；未设置时不发生带宽拥塞 | [Card.bw_cap](../util/card.py)，由 CLI `--bw-cap` 注入 |
| `bw_max` | scheduler 层 forecast 阈值：STPS Stage 2 用来找 offset；不是 engine 限流开关 | [STPSScheduler.bw_max](../schedule/stps.py)，由 CLI `--bw-max` 注入 |
| `fp.E_eff[t]` | 任务第 t 个 quantum 的通信需求；来自 `effective_traffic_trace(fp)` | [fingerprint.effective_traffic_trace](../fingerprint/__init__.py) |
| `task.next_trace_quantum()` | 读取当前 `tick_index` 对应的 `fp.E_eff`，但不推进 trace | [util/task.py](../util/task.py) |
| `task.pending_traffic` | 任务自带的 *单槽 NoC 队列*：上一 tick 没发完的残量 | per-task scalar |
| `task.tick_index` | 任务自己 trace 走到第几格；**只有 quantum 全发完才推进** | per-task counter |
| `task.start_offset` | 任务被推迟入场的 tick 数；静态算法恒 0，STPS / phase wrapper 可为 0..D_max | scheduler 在 admit 时写入 |
| `task.blocked_ticks` / `task.congestion_wait_ticks` | 阻塞累计 / 拥塞等待累计 | per-task counter |
| `_card_epoch_load[i]` | 卡 i 当 tick 实际服务字节数，即 served traffic | engine 簿记 |
| `_card_epoch_demand[i]` | 卡 i 当 tick 请求字节数，即 pre-cap demand | engine 簿记 |
| `_card_epoch_backlog[i]` | 卡 i 当 tick 结束后所有任务 `pending_traffic` 之和 | engine 簿记 |

`bw_cap` 和 `bw_max` 的边界很重要：**只有 `bw_cap` 会让 engine 产生真实拥塞**。如果只设置 `--bw-max` 而不设置 `--bw-cap`，STPS 会按该阈值做 forecast，但 `_tick` 仍然无限带宽服务，`avg_congestion_ratio` 近似为 0。CLI 当前有保护逻辑：当用户设置 `--bw-cap` 且 `--bw-max` 仍是默认值时，会自动令 `bw_max = bw_cap`，让 forecast 阈值和真实执行 cap 对齐。

steady-state 默认窗口：`steps > 128` 时裁剪首尾各 64 tick，详见 [metrics.md §0](metrics.md)。

### A.2 形式化模型

设卡 `c` 上的任务集合为 `T_c(t)`。任务 `j` 在 tick `t` 的请求量先由队列残量决定，再由 trace quantum 决定：

$$
d_j(t)=
\begin{cases}
0, & t < placement_j + start\_offset_j \\
pending_j(t), & pending_j(t) > 0 \\
E^{eff}_j[tick\_index_j], & otherwise
\end{cases}
$$

卡级 pre-cap demand 为：

$$
D_c(t)=\sum_{j\in T_c(t)} d_j(t)
$$

如果 `bw_cap` 未设置或 `D_c(t) <= bw_cap_c`，则不拥塞：

$$
S_c(t)=D_c(t),\qquad scale_c(t)=1
$$

如果 `D_c(t) > bw_cap_c`，则卡级 served traffic 被截断，所有任务按比例公平分摊：

$$
S_c(t)=bw\_cap_c,\qquad scale_c(t)=\frac{bw\_cap_c}{D_c(t)}
$$

任务级实际服务量和入队残量为：

$$
s_j(t)=d_j(t)\cdot scale_c(t),\qquad leftover_j(t)=d_j(t)-s_j(t)
$$

当 `leftover_j(t) > 0` 时，`pending_traffic` 被写成该残量，`tick_index` 和 `duration_steps` 都不推进；当 `leftover_j(t)=0` 时，当前 quantum 才算发完，任务进入下一格 trace。也就是说，拥塞的物理代价不是“本 tick 统计上少服务一点”这么轻，而是直接把任务生命周期拉长。

### A.3 单 tick 服务流程

```text
on each simulation tick t:

# ── ① 收集任务级 demand ────────────────────────────────────────────
task_demand ← {}
for task in active_tasks:
    # 静态算法 start_offset = 0，这一支永不进；STPS 可能进
    if task.start_offset > 0 and t < task.placement_step + task.start_offset:
        task.current_traffic ← 0
        continue                         # 任务“尚未出生”，不参与本 tick
    if task.pending_traffic > 0:
        d ← task.pending_traffic         # 队列残量 *优先*
    else:
        d ← task.next_trace_quantum()    # fp.E_eff[tick_index]
    task_demand[task] ← d

# ── ② 按卡聚合需求 ───────────────────────────────────────────────
for each card c:
    d_tick[c] ← Σ_{task ∈ c.tasks} task_demand.get(task, 0)
    _card_epoch_demand[c] += d_tick[c]

# ── ③ 应用 per-card 带宽上限 (fair-share scaling) ────────────────
for each card c:
    d   ← d_tick[c]
    cap ← c.bw_cap
    if cap is None or d ≤ cap:
        served_total ← d
        scale        ← 1.0                              # 不拥塞：全员全发
    else:
        served_total ← cap
        scale        ← cap / d                          # 拥塞：按比例公平分摊
    _card_epoch_load[c] += served_total

    # ── ④ 写回队列残量 + gating trace 推进 ───────────────────────
    backlog_total ← 0
    for each task tk in c.tasks:
        if tk ∉ task_demand: tk.current_traffic ← 0; continue
        d_tk     ← task_demand[tk]
        served   ← d_tk × scale
        leftover ← d_tk − served
        tk.current_traffic ← served

        if leftover > 0 and cap is not None:
            tk.pending_traffic        ← leftover         # ★ 入 NoC 队列
            tk.blocked_ticks          += 1
            tk.congestion_wait_ticks  += 1
            backlog_total             += leftover
            # 超时断路器：防止死锁，强清残量并跳过该 quantum
            if tk.blocked_ticks > MAX_BACKLOG_TICKS:
                metrics.congestion_timeouts += 1
                tk.pending_traffic ← 0
                tk.blocked_ticks   ← 0
                tk.advance_trace_tick()
        else:
            tk.pending_traffic ← 0
            tk.blocked_ticks   ← 0
            tk.advance_trace_tick()                      # quantum 已结清，trace 推进
    _card_epoch_backlog[c] ← backlog_total
```

### A.4 七条建模性质

1. **scheduler-agnostic**：`_tick` 不接受任何调度器信号；rr 与 stps 跑的是字节一致的代码。调度差异只通过 *task 在哪张卡 / start_offset 是几* 间接体现在 `d_tick` 直方图上。
2. **fair-share scaling**：拥塞时 `scale = cap / d` 等比例瓜分，不偏袒大流量或小流量任务，避免引入额外的策略变量。这让 "拥塞" 成为纯粹的总量过载现象，不掺入服务质量分级。
3. **per-task 单槽队列**：`pending_traffic` 是任务字段而非中央 FIFO。整张卡的 backlog = `Σ_tasks pending_traffic`。新 quantum 不会在旧 quantum 排空前被生成（① 处 `pending_traffic > 0` 时跳过 `next_trace_quantum`）。
4. **trace 推进被 gating**：`tk.advance_trace_tick()` 只在 leftover=0 时跑。一个 trace 长 T_quantum 的任务，若每 tick 拿到 `scale` 比例的带宽，实际占用约 `T_quantum / scale` tick 完成 trace。**这是吞吐损失的物理机理**。
5. **`duration_steps` 与 trace 解耦**：任务完成需同时满足 trace 喂完 + duration 倒计时归零 + pending 排空（[engine.py `_handle_completions`](../simulation/engine.py)）。重 cap 下 duration 通常先到 0，任务仍要等 NoC 排空才算正常完成。
6. **超时断路器是 anti-deadlock 边界**：`MAX_BACKLOG_TICKS` 设得远大于 Q0/traffic 观测的 `mean_cong_wait ≈ 8-9 ticks`；当前实验中 `congestion_timeouts = 0`，故 `completion_rate = 1.000`。一旦触发，残量被丢弃，throughput 下降但 completion_rate 不变 —— 这是模型层的兜底，论文比较时应一并报。
7. **steady-state 截断在 metrics 层**：`_tick` 写满 [0, steps)；裁剪发生在 [util/metrics.py `_steady_window`](../util/metrics.py#L213)。引擎不关心比较窗口。

### A.5 静态算法 vs STPS：差异如何传到拥塞指标

静态算法 (rr / bestfit / drf / p2c)：

- `start_offset = 0` 恒成立 → ① 处的早期 `continue` 永不触发。
- 任务从 `placement_step` 立刻开始按 trace 发流量，无任何避峰。
- 同一张卡上若多任务的 `fp.E_eff` spike 落在同一 tick，`d_tick` 直接相加 → 极易 ≫ cap → `scale ≪ 1` → 大量 leftover 入 `pending_traffic` → `congestion_wait_ticks` 累积。
- 调度器只优化 *空间维*（哪张卡上有几个任务），无法控制 *时间维* (spike 在哪个 tick 撞)。

STPS：

- `task.start_offset = Δt*` 由 Stage 2 在 admission 时给出（详见后文 §3）。
- ① 处前 Δt* 个 tick 该任务 demand=0，等到 trace 真正开始喂入时，已被 forecast 错峰到候选卡的低谷上。
- 同一张卡的 `d_tick` 直方图方差更小 → `scale` 平均更接近 1 → leftover 入队的高度 / 持续时间都更短。

净效应：`avg_congestion_ratio` 与 `mean_congestion_wait_ticks` 在 STPS 上稳定低于 baseline 5%-9%（[Q0_result.md §3.3](Q0_result.md)），且这套机制对 baseline 完全公平 —— 拥塞下降不靠任何引擎层偏袒，纯粹靠 `start_offset` 把 demand 时间分布拉平。

### A.6 与 metric 计算的对接

引擎层 `_card_epoch_{demand, load, backlog}` 三元组在每个 tick 末被 [SimulationMetrics.record_load_snapshot](../util/metrics.py#L163) 拍快照，进而派生：

- `avg_congestion_ratio = mean_{i,t}((d - s) / d)` ← [metrics.md §2.1](metrics.md)
- `peak_backlog = max_{i,t} backlog` ← [metrics.md §2.3](metrics.md)
- `avg_utilization = mean_{i,t}(s / cap)` ← [metrics.md §2.4](metrics.md)
- `mean_congestion_wait_ticks = mean over tasks(task.congestion_wait_ticks)` ← [metrics.md §2.5](metrics.md)

引擎只产生原始三元组与任务级计数器，所有分位 / 均值 / CV 计算都在 metrics 层完成。这保证后续可加 [metrics.md §1b](metrics.md) 的时间维负载均衡而无需改 `_tick`。

### A.7 模型能力边界

这套拥塞模型的优势是 **公平、轻量、可比较**：所有 scheduler 共享同一条 `_tick` 服务路径；超额流量转化为 pending 队列和等待时间，而不是直接 drop；`demand / served / backlog` 三个口径能区分原始通信压力、实际服务曲线和排队状态。

但它仍是卡级聚合模型，不能过度解释为真实 NoC microarchitecture：

1. **无拓扑与路由**：没有 mesh/crossbar 拓扑、hop count、路由策略或链路级路径冲突。
2. **无 packet/flit 机制**：没有 router buffer、virtual channel、head-of-line blocking 或 packet serialization。
3. **无 source-destination pair**：通信需求只体现为卡级总注入量，不能表达 “A 到 B” 的通信矩阵。
4. **无服务优先级**：拥塞时所有任务按 `scale = cap / demand` 等比例缩放，没有 FIFO 顺序、优先级或 QoS class。
5. **队列是 per-task 单槽**：`pending_traffic` 只保存当前 quantum 残量，不是中央多元素 FIFO；它能表达 backlog 和等待，但不能表达任务间严格排队顺序。
6. **cap 是静态标量**：`bw_cap` 不随时间、温度、链路状态或 background traffic 动态变化。
7. **timeout 是模型断路器**：一旦 `blocked_ticks > MAX_BACKLOG_TICKS`，残量会被强制清空并计入 `congestion_timeouts`。当前 Q0/Q-traffic 实验中该值为 0，因此主结论仍是排队语义；若未来触发，需要单独报告。

因此，本文档中 “通信拥塞下降” 的含义应严格表述为：**卡级 pre-cap demand 超过 per-card injection cap 的比例下降、pending backlog 和任务级 congestion wait 下降**；它不等价于链路级 NoC 仿真中的路径拥塞、router stall 或 packet latency 全面下降。

---

## 0. 符号

| 符号 | 含义 | 来源 |
| --- | --- | --- |
| `H` | forecast horizon (ticks) | CLI `--horizon`，默认 64 |
| `D_max` | Stage B 允许的最大 start_offset (ticks) | CLI `--d-max`，默认 2 (Q0/traffic 调谐后) |
| `BW_max` | Stage B forecast 内带宽阈 (per-card, B/tick) | CLI `--bw-max`，默认 9.0e5 |
| `θ` | 热点切分中心性阈值 | CLI `--centrality-split-threshold`，默认 0.2 |
| `α_β` | β EMA 平滑系数 | 代码默认 0.3 |
| `c_i.E[0..H)` | 第 `i` 张卡的滚动 forecast traffic timeline | `Card.forecast` |
| `c_i.β` | 第 `i` 张卡的累计 burstiness EMA | `Card.beta_card` |
| `c_i.frag` | 第 `i` 张卡的最大空闲连续块占比 ∈ [0,1] | `Card.largest_free_block_ratio()` |
| `fp.E_eff[0..T)` | 任务有效流量轨迹 `mean_injection_trace × state_size_mb` | `fingerprint.effective_traffic_trace(fp)` |
| `fp.β_global` | 任务全局 burstiness | `fp.global_burstiness` |
| `fp.K̄` | 任务平均活跃连通分量数 | `fp.mean_components` |
| `fp.c_last` | 任务末步 in-eigenvector centrality 向量 | `fp.max_centrality` |

---

## 1. 顶层流程：`select_card_for_task(task)`

```text
Input : task (with fingerprint or path), self.cards
Output: chosen card or None (resource-infeasible)

1. fp ← resolve_fingerprint(task)              # 懒加载 .npz
2. candidates ← {c ∈ cards : c.can_host(task)} # 资源筛 (cores / mem)
3. if candidates = ∅ : return None
4. if USE_STAGE1 and fp ≠ None :
       candidates ← Stage1_filter(candidates, fp)   # 按 (frag, β) 重排
5. if USE_STAGE2 and fp ≠ None :
       (chosen, Δt*, peak*) ← Stage2_phase_shift(candidates, fp)
       if chosen = None : return None
       # admission 关键改造：不再 reject
       if peak* > BW_max :
           log_debug("over-budget; min-peak offset Δt*, NoC queue absorbs")
       task.start_offset ← Δt*
       chosen.add_forecast(fp.E_eff, offset=Δt*)
   else :
       chosen ← candidates[0]
6. if USE_STAGE3 and fp ≠ None :
       task.split_plan ← split_population(fp.c_last, θ)
7. if fp ≠ None :
       chosen.β ← EMA(chosen.β, fp.β_global, α_β)
8. return chosen
```

> 第 5 步的 `if peak* > BW_max:` 分支是 [traffic_result.md §3](traffic_result.md) 改造的核心：旧版本在此处置 `task.rejected = True; return None`，导致超额任务被丢弃、`completion_rate` 在重负载下塌到 0.5-0.9。新版本只记录 debug 日志、把任务放进卡，超出部分由引擎层的 NoC `pending_traffic` 队列吸收（见 §5）。

---

## 2. Stage 1 — Macro Card Dispatching

按两条物理直觉给卡评分，分越低越优先：

- **碎片匹配 `frag_score`**：每张卡当前最大空闲连续块占比 `block_i`；任务的目标块占比 `target = 1/max(K̄,1)` —— K̄ 越大代表任务越能拆分，target 越小，越乐意放进碎片化的卡。`frag_score = |block_i − target|`。
- **β 隔离惩罚 `β_penalty`**：当 `fp.β_global > β_high_threshold` (默认 1.5) 时，惩罚 = `c_i.β`，把高 burstiness 的任务避开已经 bursty 的卡；否则惩罚 = 0。

```text
Stage1_filter(candidates, fp):
    K       ← max(fp.K̄, 1)
    target  ← 1 / K
    scored  ← []
    for c in candidates:
        frag    ← | c.frag − target |
        β_pen   ← c.β  if fp.β_global > β_high_threshold else 0
        score   ← w_frag · frag + w_β · β_pen
        scored.append((score, c))
    sort scored by score ascending
    return [c for _, c in scored]            # 保留全部，仅重新排序
```

注意 Stage 1 不再裁剪候选集，只是给 Stage 2 一个 "优先看哪些卡" 的顺序。

---

## 3. Stage 2 — Micro Temporal Phase-Shifting (Algorithm 1)

对每张卡，搜索最佳 `Δt ∈ [0, D_max]`，使得 `peak(c.E + shift(fp.E_eff, Δt))` 最小且 ≤ `BW_max`；选 peak 最低的 (card, Δt)。

```text
find_optimal_offset(E_m, E_new, D_max, BW_max):
    H        ← len(E_m)
    best_dt  ← -1
    best_pk  ← +∞
    # Pass 1: 寻找首个进入 BW_max 带内的 offset
    for dt in 0..D_max:
        shifted ← zeros(H); shifted[dt : min(H, dt+|E_new|)] ← E_new[:...]
        pk      ← max(E_m + shifted)
        if pk ≤ BW_max and pk < best_pk:
            best_pk ← pk
            best_dt ← dt
    if best_dt ≠ -1:
        return (best_dt, best_pk)
    # Pass 2: 无可行解 → 返回 min-peak fallback
    fb_dt, fb_pk ← argmin_{dt ∈ 0..D_max} max(E_m + shift(E_new, dt))
    return (fb_dt, fb_pk)                   # fb_pk 可能 > BW_max
```

```text
Stage2_phase_shift(candidates, fp):
    best ← None
    for c in candidates:
        c.ensure_forecast(H)
        (dt, pk) ← find_optimal_offset(c.E, fp.E_eff, D_max, BW_max)
        if best = None or pk < best.peak:
            best ← (c, dt, pk)
    return best                              # (None,0,+∞) 仅在 candidates 空时
```

`D_max = 2` 的语义：每个任务最多被推迟 2 ticks 等空窗。这是 [traffic_result.md §5](traffic_result.md) 在保证 STPS 吞吐下降不超过静态基线的前提下选出的最大 "避峰激进度"。

---

## 4. Stage 3 — Micro Spatial Mapping with Hotspot Splitting

```text
split_population(c_last, θ):
    if |c_last| = 0 : return []
    return [ i for i, v in enumerate(c_last) if v ≥ θ ]
```

θ=0.2 把末步 centrality ≥ 0.2 的神经元 population 标记为需要跨 PIM core 切分。该列表存到 `task.split_plan`，物理摆放仍由下层 Card 模型负责。

---

## 5. Admission contract & Layer 2 contention-avoidance

新算法在 [schedule/stps.py:98-115](../schedule/stps.py#L98-L115) 落地：

```python
chosen, offset, peak = self._stage2_phase_shift(candidates, fp)
if chosen is None:
    return None
if peak > self.bw_max:
    logger.debug(
        "[STPS] Task %s peak %.2f exceeds BW_max %.2f; "
        "using min-peak offset %d, NoC queue absorbs overflow",
        task.task_id, peak, self.bw_max, offset,
    )
task.start_offset = int(offset)
chosen.ensure_forecast(self.horizon)
chosen.add_forecast(effective_traffic_trace(fp), offset)
```

**这一段做了什么 (Layer 2 contention-avoidance cost)**：

1. **Stage 2 已挑出 min-peak offset**：在 `peak > BW_max` 时 `find_optimal_offset` 会进入 Pass 2，返回 `D_max` 范围内 peak 最低的那个 offset。注意这里 STPS *仍在错峰*，只是错不到 cap 以下而已 —— Δt 越大，任务越晚到达，越能错开其他任务的峰。
2. **不 reject**：旧版在此处 `task.rejected = True; return None`，新版只记一行 debug 日志。完成率不再被 STPS 自己折损。
3. **写 forecast 与 start_offset**：`add_forecast(fp.E_eff, offset=Δt*)` 把任务流量叠到卡的 H-步 forecast 上；`task.start_offset = Δt*` 让 [simulation/engine.py](../simulation/engine.py) 在 task placement 后的前 Δt* 个 tick 跳过 `simulate_tick`，让任务 "晚出生"。
4. **NoC `pending_traffic` 吸收超额**：当任务 tick 时实际产生的 traffic 超过 engine 的 `bw_cap` 时，超出部分进入 `Task.pending_traffic` 队列、按 `bw_cap` 速率排出（详见 [simulation/engine.py](../simulation/engine.py) 的 `_tick`）。这是 STPS *无法在 forecast 阶段消化* 的尖峰最终被吸收的地方，体现为 `congestion_ratio` / `mean_congestion_wait_ticks` 上升 —— 但 STPS 由于已经把 Δt* 推到 min-peak，进入队列的高度 / 持续时间相比 baseline 小，因此 [Q0_result.md §3.3](Q0_result.md) 看到 cong_ratio -5%~-8%、cong_wait -7%~-9% 的稳定收益。

**Layer 2 算法伪代码**（把 §1-§5 浓缩为一个 admission flow）：

```text
admit_under_cap(task, fp, candidates, D_max, BW_max):
    # Stage 2 主路：尝试在 cap 下避峰
    (chosen, Δt*, peak*) ← Stage2_phase_shift(candidates, fp)
    if chosen = None : return REJECT_RESOURCE          # 仅当资源不够

    if peak* ≤ BW_max:
        # Layer 1 only: Stage B 已把组合 peak 压到 cap 内
        # 任务带 Δt* 入卡，无后续 NoC 排队
        commit(chosen, task, Δt*, fp.E_eff)
        return ADMITTED_CLEAN

    else:
        # Layer 2 active: forecast 仍超 cap，但 Δt* 已是 min-peak fallback
        # 不 reject；engine 的 NoC pending_traffic 队列在运行期吸收溢出
        commit(chosen, task, Δt*, fp.E_eff)
        return ADMITTED_OVERFLOW

where commit(c, task, Δt, E_eff):
    task.start_offset ← Δt
    c.ensure_forecast(H)
    c.add_forecast(E_eff, offset=Δt)
```

`ADMITTED_OVERFLOW` 分支即 **Layer 2 contention-avoidance cost** 的算法层入口：STPS 主动选择 "min-peak 推迟入卡 + 让 NoC 队列吸收" 而不是 "丢弃任务"，这把代价从 completion_rate 折损转为 congestion-wait 折损，再叠在 Stage B 本身的 intrinsic offset 上 (Layer 1)。两层成本的实证拆分见 [traffic_result.md §4](traffic_result.md)。

---

## 6. 每 tick 维护：`step(t)` 与 `on_task_completion`

```text
step(t):
    for c in cards:
        c.advance_forecast()         # E ← roll_left(E, 1); E[H-1] ← 0

on_task_completion(task, t):
    pass                             # forecast 已自然滚动；β EMA 在 admit 时已更新
```

`advance_forecast` 让 forecast 始终对齐 "当前 tick 起向前看 H 步"。Stage 2 在 `select_card_for_task` 时把新任务的 E_eff 叠到对应卡的 forecast 上，下个 tick 再整体左移一格。

---

## 7. 关键参数与当前默认值

| 参数 | 当前默认 | 选择依据 |
| --- | --- | --- |
| `H` (horizon) | 64 | 等于多数 fp 的 `T`，覆盖一个完整推理周期 |
| `D_max` | 2 | [traffic_result.md §5](traffic_result.md) 调谐：保证 STPS 吞吐下降 ≤ 静态基线 |
| `BW_max` | 9.0e5 | bursty+mixed 指纹未限流下 per-card demand p75 ([traffic_calib.md](traffic_calib.md)) |
| `θ` | 0.2 | paper §4.3 默认 |
| `α_β` | 0.3 | 代码默认；半衰期 ~2 个任务 |
| `β_high_threshold` | 1.5 | 仅当任务自身 burstiness 超过 1.5 才启用 β 隔离惩罚 |

---

## 8. 与 paper §4.3 的差异

| 项 | paper §4.3 | 当前实现 |
| --- | --- | --- |
| Stage 2 超 BW_max | reject + drop | min-peak offset 入卡，NoC 队列吸收 |
| Stage 1 输出 | top-k 候选裁剪 | 全部候选按 (frag, β) 重排，Stage 2 看全部 |
| `D_max` | 16 (默认) | 2 (Q0/traffic 调谐后) |
| Stage 3 物理放置 | 跨 core 切分 | 仅记录切分列表，下层 Card 模型负责 |

Paper 描述的"硬拒"语义在重负载真实指纹下会把 STPS 自身的 `completion_rate` 拖到 < 0.9 (详见 [traffic_result.md §3](traffic_result.md))，导致 STPS 的 p99/throughput 与 baseline 不可比。Layer 2 admission 改造的目的就是把 STPS 与 baseline 拉回 `completion_rate = 1.000` 的同一比较平面上。

---

## 9. NoC 队列 (`pending_traffic`) 的作用

承接 §5 “min-peak 推迟入卡 + 让 NoC 队列吸收” —— 这一节具体讲 **被吸收的部分到底发生了什么**。代码入口：[simulation/engine.py `_tick`](../simulation/engine.py#L309-L378)。

### 9.1 角色与字段

| 字段 / 量 | 类型 | 含义 |
| --- | --- | --- |
| `Card.bw_cap` | 标量 (B/tick) | 卡级硬带宽上限；engine 用它做 per-tick service。Q0 设置为 `9e5`。 |
| `Task.pending_traffic` | 标量 (B) | **NoC 队列状态**：上一 tick 没排空的流量残量。这一字段就是 “NoC 队列” 的本体 —— 每个任务自带一个单槽的 queue head。 |
| `Task.current_traffic` | 标量 (B/tick) | 本 tick 实际被服务的流量。 |
| `Task.blocked_ticks` | 计数 | 已经被阻塞了几个 tick（驱动 timeout 断路器）。 |
| `Task.congestion_wait_ticks` | 计数 | 累计 “排队等带宽” 的 tick 数；进入 Q0/Q-traffic 报表的 `mean_congestion_wait_ticks`。 |
| `Task.tick_index` | 计数 | 任务自身 trace 走到第几个 tick；只有 **完全排空** 当前 quantum 才推进。 |

队列只有一个槽位，因为每个任务每 tick 只产生一个 quantum；新 quantum 不会在旧 quantum 排空前生成。

### 9.2 单 tick 的服务流程

```text
on each tick t, for each card c:

  # ---------- ① 收集需求 ----------
  demand_c ← 0
  for task tk in c.tasks:
      if tk.start_offset > 0 and t < placement + tk.start_offset:
          continue                              # STPS Stage 2 强制 “晚出生”
      if tk.pending_traffic > 0:
          d ← tk.pending_traffic                # 队列残量优先（已经在 NoC 等了）
      else:
          d ← tk.next_trace_quantum()           # fp.E_eff 的下一格
      demand[tk] ← d
      demand_c += d

  # ---------- ② 比较卡的 bw_cap ----------
  if demand_c ≤ bw_cap:
      scale ← 1.0                               # 不拥塞：全员全服务
      served_total ← demand_c
  else:
      scale ← bw_cap / demand_c                 # 拥塞：按比例公平分摊
      served_total ← bw_cap

  # ---------- ③ 服务 & 写回队列残量 ----------
  for task tk in c.tasks:
      served    ← demand[tk] · scale
      leftover  ← demand[tk] − served
      tk.current_traffic ← served
      if leftover > 0:
          tk.pending_traffic ← leftover         # ★ 入队（实质就是 “没排干净”）
          tk.blocked_ticks   += 1
          tk.congestion_wait_ticks += 1
          if tk.blocked_ticks > MAX_BACKLOG_TICKS:
              # ④ 超时断路器：强制清空 + 计入 metrics.congestion_timeouts
              tk.pending_traffic ← 0
              tk.blocked_ticks   ← 0
              tk.advance_trace_tick()           # 跳过这个 quantum
      else:
          tk.pending_traffic ← 0
          tk.blocked_ticks   ← 0
          tk.advance_trace_tick()               # 该 quantum 已结清，trace 推进

```

### 9.3 关键性质

1. **队列即任务自身字段**：`pending_traffic` 不是中央 FIFO，是 *per-task 单槽*。整张卡所有任务的 `pending_traffic` 之和 = 该卡当 tick 的 backlog（写入 `_card_epoch_backlog`）。
2. **fair-share scaling**：拥塞时所有任务按 `scale = bw_cap / demand` 等比例瓜分带宽 —— 不偏袒大流量任务也不偏袒小任务。
3. **trace 推进被 gating**：只有 `pending_traffic` 完全排空，`tk.advance_trace_tick()` 才会跑，任务的 `tick_index` 才会 +1。这意味着 **一个本应 16 ticks 跑完的任务，如果每 tick 只能拿到 50% 带宽，会变成 ~32 ticks 才推进完 trace** —— 这就是吞吐损失出现在 STPS 与 baseline 表里的物理机理。
4. **`duration_steps` 与 trace 解耦**：只有任务的 trace 全部 fed (`tick_index` 走完) **而且** `duration_steps` 倒计时到 0 才完成 (`_handle_completions`)。在重 cap 下后者通常先到 0，但任务仍要等 `pending_traffic` 排空才被视为正常完成；中途若触发 §9.2 ④ 的超时断路器，残量被强制丢弃并计入 `metrics.congestion_timeouts`，避免死等。
5. **timeout 断路器是 anti-deadlock 边界**：默认 `MAX_BACKLOG_TICKS` 设为引擎中较大值（远超观测到的 `mean_cong_wait ≈ 8-9 ticks`）。Q0/traffic 实验里 `congestion_timeouts` 实际触发数 = 0，所以 completion_rate 才能稳定 1.000 —— **NoC 队列在当前 cap 下绝对值上未撑爆，只是显著拉长每任务延时**。

### 9.4 STPS 与 baseline 在 NoC 队列上的差异

baseline (rr / bestfit / drf / p2c) **无 forecast**，每 tick 任务按原始 trace 一齐发车；当 fp 尖峰落在同一 tick 时，`demand_c >> bw_cap`、`scale << 1`、`pending_traffic` 暴增。

STPS 在 admission 阶段已经做了两件 NoC 友好的事：

- **Stage 1 错卡**：高 β 任务被 β_penalty 推开拥塞热卡，降低单卡 `demand_c` 的 spike 振幅。
- **Stage 2 错时 (Δt*)**：min-peak offset 把任务的 spike 移到候选卡 forecast 的低谷；即便 cap-overflow 不可避免，**叠加位置** 也比 baseline 更分散。

所以同样的 `bw_cap = 9e5` 下，STPS 的每 tick `demand_c` 直方图 **方差更小**：

- 平均 `scale` 离 1.0 更近 → `mean_congestion_wait` 低 -7%~-9%
- 进入队列的 leftover 平均更小 → `peak_backlog` 略低（Q0 main 表：STPS 2.18M vs bestfit 2.16M 相近，bursty 下 STPS 2.18M vs rr 2.09M 略高 —— 即峰值 backlog 并非 STPS 强项，**持续时间** 才是）
- `congested_card_tick_frac` 低 ~2pp → 拥塞窗口本身更短

这些都是 [Q0_result.md §3.3](Q0_result.md) 的 cong_ratio / cong_wait 收益的直接来源。

### 9.5 一句话总结 NoC 队列的作用

> NoC 队列 (`Task.pending_traffic`) 是带宽冲突时的 **任务级缓冲单槽**：它把 “这一 tick 本想发 D B、但 bw_cap 只给我 D·scale B” 的差额 `(1-scale)·D` 暂存起来，下一 tick 优先重发，并 gating 任务自己的 trace 推进。STPS 的 Stage B 通过 “min-peak Δt* 入卡” 让进入这个缓冲槽的流量更小、停留更短，从而把超额任务从 “丢弃” 转为 “排队”，再把排队从 “长” 转为 “短” —— 这就是 Layer 2 contention-avoidance cost 在引擎里的物理过程。
