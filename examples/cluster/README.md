# 四卡 baseline 演示输入

两个场景使用相同的 24 个任务、相同任务顺序和 workload，仅 `arrival_tick` 不同。每张卡为 `4 × 4` Mesh，共 16 个物理核；任务完整部署在一张卡内。所有策略使用相同的 row-major 规则，将 MicroPopulation 按索引映射到当前可用的最小核 ID，核 ID 为 $y\cdot4+x$。

这些输入为人工构造的稀疏时变刺激，没有真实 SNN 推理或硬件测量来源。SOP 序列与流量序列独立指定。特别是 `compute_heavy`，计算工作较高但通信较低，可检查策略是否分别考虑计算与通信。

| workload | MicroPopulation 数 | 逻辑 steps | 总 SOP | 跨核 flit | 本地 flit | 平均 SOP/step | 平均 NoC endpoint/step |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `light_sparse` | 4 | 8 | 1080 | 25 | 2 | 135 | 6.25 |
| `compute_heavy` | 4 | 8 | 3000 | 8 | 3 | 375 | 2 |
| `fan_in` | 6 | 8 | 1600 | 56 | 2 | 200 | 14 |
| `wide_burst` | 8 | 8 | 1800 | 74 | 3 | 225 | 18.5 |

每种 workload 重复 6 次，因此两种到达模式各有 44,880 SOP、978 个跨核 flit 和 60 个本地 flit。四种 workload 都在第 5 个逻辑 step 出现主要通信突发；其他 steps 包含静默、较小脉冲和本地通信。`fan_in` 与 `wide_burst` 有多个源向相同目的 MicroPopulation 汇聚，形成共享目的 NI 的争用，路径取决于实际可用核位。

任务的 `mean_compute_sops` 和 `mean_noc_endpoint` 是声明的离线校准信息。该合成演示采用已知 profile 的均值：

$$
\overline C=\frac{\sum_{s,p} C_{s,p}}{T},\qquad
\overline N=\frac{2\sum_{s,e:\operatorname{src}(e)\ne\operatorname{dst}(e)} F_{s,e}}{T}.
$$

一个跨核 flit 对应源 Tx 和目的 Rx 两次 endpoint 活动，本地 flit 不进入 NoC。均值是每个逻辑 step 的 offered 工作量，排队顺延后的实际物理 Tick 吞吐另由仿真输出。调度策略只收到这些任务级均值，逐 step 的 replay 流量属于仿真器输入。此合成场景均值恰好与 replay profile 相同，不构成真实数据上的预测准确性实验。

| 参数 | 当前值 |
| --- | --- |
| 卡数 / 每卡 Mesh | 4 / `4 × 4` |
| 每核容量 / 每卡状态内存 | 64 neurons / 8 MB |
| 计算 / NoC 评分归一化预算 | 500 SOP / 40 endpoint |
| 每个物理 Tick | 8 NoC cycles |
| Router / 源 NI / 目的 NI 队列深度 | 2 / 2 / 2 |
| 目的 NI 消费间隔 | 1 cycle |
| 最大物理 Tick / 预设稳态窗口 | 400 / `[5, 40]` |
| 调度随机种子 | 17 |

`poisson.json` 使用速率 1.2 tasks/物理 Tick，种子 23。生成器先采样独立指数间隔，再用 `floor(time)+1` 将连续 Poisson 到达分箱到 1-based Tick；这与每个完整 Tick 的独立 Poisson 到达计数相对应，得到 24 个任务后停止采样。具体时刻为：

`1, 1, 1, 2, 3, 8, 8, 9, 9, 9, 10, 10, 10, 10, 10, 12, 15, 15, 16, 17, 17, 19, 20, 23`。

`bursty.json` 在 Tick 1、7、13、19 各到达 6 个任务。到达不受卡是否堵塞影响；资源不足造成的等待由 `placement_tick - arrival_tick` 衡量。

可用 [simulation/arrivals.py](../../simulation/arrivals.py) 再生成到达时刻：

```python
from simulation.arrivals import make_arrival_ticks

poisson = make_arrival_ticks("poisson", 24, seed=23, poisson_rate=1.2)
bursty = make_arrival_ticks("bursty", 24, burst_size=6, burst_interval=6)
```

输入元数据记录到达模式、随机种子、校准定义和合成来源。跨策略比较应直接复用同一份场景文件，避免重新随机生成任务。
