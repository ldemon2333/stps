# RFC：SNN NoC 仿真、多卡基线与联合 STPS

状态：**单卡模拟器、五基线与卡＋偏移联合STPS已实现；120次同二进制热点比较未证明全面均衡优势**。单卡入口提供显式 mapping/start_tick，多卡入口按外生 arrival_tick 在线选卡、共用 row-major mapper。当前源码和命令见 [架构](arch.md)、[实验](experiment.md)；单卡验证见 [elastic](results/elastic.md)，五基线功能验收见 [cluster_baselines](results/cluster_baselines.md)，当前多维比较见[stps_hotspots](results/stps_hotspots.md)。旧每卡总量 FIFO、历史调度器、聚合指纹兼容入口及旧实验脚本已删除；新 baseline 使用独立资源快照，历史论文数值不能解释为新网络结果。

## 1. 研究目标与当前边界

每张卡是独立部署实例，每个 Task 完整放在一张卡上，**没有跨卡发包或跨卡路由**。“卡间负载均衡”指多张独立卡在同一物理时间窗口中的负载均衡。计算工作量与卡内通信工作量分别量化，不假定计算活动与通信活动成正比。

第一阶段通过预设场景观察 SNN 高度稀疏、随逻辑步变化的 SOP 和发包，以及多任务共享 NoC 的竞争。单卡场景提供 Task ID、工作负载、开始物理 Tick 和 MicroPopulation→core_id 映射；手动修改开始 Tick 是输入对照。

当前新增多卡基线：RR、WorstFit、DRU、BestFit、P2C-Mean 在外生到达时执行在线选卡，所有策略使用同一固定 mapper 和 NoC。一个物理 Tick 各卡各运行 K cycle，逻辑轮次与 Rx 屏障彼此独立。联合STPS已实现，schema支持profile及stps配置，联合选择有限偏移；五基线phase_offset仍0，MeanLoad-Dual未实现。

```bash
python main.py --cluster-scenario examples/cluster/poisson.json --policy RR --output-dir data/new_cluster_run --trace
python script/validate_cluster.py --output-root data/cluster
```

外部参照固定为 [Noxim 提交 1b7d999](https://github.com/davidepatti/noxim/tree/1b7d99923040ef5bda15ec519ef0071edf2dfad8)。其 [ProcessingElement](https://github.com/davidepatti/noxim/blob/1b7d99923040ef5bda15ec519ef0071edf2dfad8/src/ProcessingElement.cpp) 区分生成请求与发送，其 [Router](https://github.com/davidepatti/noxim/blob/1b7d99923040ef5bda15ec519ef0071edf2dfad8/src/Router.cpp) 展示缓冲、路由和接收路径。本实现参考这些职责，独立实现 Python 单 flit 消息、单 VC、二维 mesh、XY 与轮转仲裁。未移植其 SystemC 实现，未声称与 Noxim 周期等价。SNN 全卡 Rx 屏障与跨 Tick 等待是本项目当前性能模型，不声称等同真实芯片硬截止或 TrueNorth 目标投递 Tick。

## 2. 最小数据模型与职责

| 对象 | 当前实现与状态 | 取舍 |
| --- | --- | --- |
| `Workload` | 不可变 NumPy 数组：MicroPopulation 大小、有向边、逐边期望 flit、逐 MicroPopulation SOP；来源、单位和元数据 | 保留源宿与独立计算轨迹，拒绝只有总量 E 的历史 NPZ |
| `Scenario / TaskPlacement` | 一张卡的配置、最早启动 Tick、固定映射、工作负载引用和哈希 | 场景校验单任务可行性；引擎按实际生命周期检查并发核位和内存，不新增调度算法 |
| TaskRequest / ClusterScenario | 外生 arrival_tick、Workload 引用/hash、显式声明的双负载均值、同构卡配置与固定稳态窗 | 与单卡 scene 独立 schema；不提供 mapping 或主动相位 |
| BaselinePolicy / CardSnapshot | 五种策略读取可行卡资源与缓存均值；自身 RR 指针/独立随机流、决策记录 | 不读取成功吞吐、NoC 队列或测试请求未来逐步轨迹 |
| CardRuntime | 各卡独立网络/进度、核内存预留、均值账本、实际启动/完成释放 | placement 立即预留；轮次边界 actual_start；末步共同屏障通过才释放 |
| `NoCNetwork` | 内部源待发计数、有限源 NI/Router 输入/目标 NI、每输出轮转指针 | 对外只有 offer、advance_cycle、库存/队列查询；没有独立持久化 Packet/Link 业务对象 |
| 引擎任务状态 | 实际起点、当前逻辑步、已完成步、完成 Tick 与延长 Tick | 逻辑步显式推进；全卡本轮全部 Rx 才共同推进，物理 Tick 继续前进 |
| `SimulationMetrics / MetricsWriter` | 从实际事件生成核、任务、整卡计数，逐 Tick 队列/链路统计，汇总和 manifest | 名称沿用文件职责，旧字段与语义全部删除；不存在 legacy 模式 |
| 报告 | CSV→独立 HTML 与 SVG | 运行时不接外部日志平台，不保留每包长期历史；逐事件 CSV 由 --trace 开启 |

源待发区按同一任务/逻辑步/边/生成时刻压缩计数；实际有限队列保留 FIFO flit 身份。大批量请求不会先展开成全部 flit 对象。单包即一个 flit，不需要多 flit 的头尾、VC 分配或重传协议。每个输入/输出最多每 cycle 传一 flit。

## 3. 离线输入：同源边流量与独立 SOP

[schema 与量化实现](../fingerprint/workload.py) 使用版本 1 JSON 或不含 pickle 的 NPZ：

| 字段 | 形状/单位 | 含义 |
| --- | --- | --- |
| `pop_size` | [N]，正整数 | 每个 MicroPopulation 的神经元数 |
| `edge_src / edge_dst` | [E]，整数索引 | 有向边，重复源宿须先求和 |
| `edge_expected_flits` | [T,E]，flit/逻辑 Tick | 非负期望通信量 |
| `compute_sops` | [T,N]，SOP/逻辑 Tick | 独立、非负计算工作量 |
| `state_size_mb` | MB | 单个任务实例占用的内存 |
| `source / metadata / units` | 字符串与 JSON 元数据 | 来源、模型/样本/换算口径、明确单位 |

T、N、E 由形状推导。数值必须有限；总量溢出拒绝。加载后按**每边逻辑时间顺序累积小数余量、向下取整**得到整数 flit。使用浮点数最短十进制表示做 carry，避免十个 0.1 少发一个 flit；一个 artifact 量化一次，被多个 Task 复用。期望总量与量化总量都写入 manifest，差额可复算，不属于丢包。SOP 不参与 flit 量化和守恒。

每个 MicroPopulation 独占一核。i=j 的自环计入 local_flits，绕过 NoC；i≠j 的边必定跨核。每步跨核 flit 总量与 SOP 和不依赖坐标，但 XY 路径、共享链路和延迟、屏障延长量依赖映射。

三条输入路径已接通：

1. 明确标为 synthetic 的稀疏测试图，独立设置计算与通信。
2. 导入同源 `W[T,N,N,2]`：通道 0 是边流量，通道 1 按目标 MicroPopulation 求和为 SOP。全零流量也保留计算。
3. [collect_spike_traces](../fingerprint/dtdg.py) 对显式命名模块采集 `T×samples×units` 脉冲，调用者提供实际 [EdgeSpec](../fingerprint/edge_builder.py) 图、MicroPopulation 切片与算子倍率；[提取器](../fingerprint/extractor.py) 对计算和通信选择同一个样本，或对同一批样本取平均。不会以模块注册顺序猜图。

第三条路径的 `compute_per_flit` 是**显式算子 SOP 代理**，不是实测服务周期，也不自动覆盖膜电位更新等全部算子。需要覆盖这些工作时，输入生成器应独立补充对应 compute_sops。小型真实 LIF 前向采样示例已验证；真实数据集、大型预训练 SNN 与硬件标定仍需后续实验，不能用合成图代替其证据。

## 4. 固定物理 Tick、弹性逻辑步与实际生命周期

物理 Tick 从1开始，NoC cycle从0开始，每 Tick 固定K cycle；任务逻辑步s从0开始，由进度状态保存，不再等于t-start_tick。

每次全卡轮次开始，全部运行任务各执行一个逻辑步：SOP记一次，在起点按轨迹生成一次请求。随后每物理Tick执行K cycle；若仍有源待发/源NI/Router未到达包，保留队列并顺延，下一Tick继续传输，所有任务不生成下一步。全卡未到达量为0时释放Rx屏障，下一物理Tick才能执行下一轮。零包步最短一个物理Tick，也可能被同卡其它任务拖长。

单卡固定场景的 start_tick 是最早启动时刻。只在轮次边界按(start_tick, task_id)尝试待任务；固定核位和内存可用才实际启动。允许不同任务指定相同核位，由运行时等待处理，资源直到实际末步屏障完成才释放。非阻塞准入可跳过暂时放不下的任务，不改变mapping或搜索相位。记录实际开始、启动等待与完成时间。

每逻辑步全部Rx但其它任务未到达时仍等待全卡屏障。最后逻辑步只有共同屏障释放才完成；sink中旧包保留身份并可在核复用后消费，消费不直接控制Rx屏障。任务完成后不额外排空已到达库存。达到max_ticks而未完成记不完整，CLI退出2；跨Tick等待本身不再触发截止失败或停止。

集群每 Tick 起点使用上一 Tick 实际完成后释放的资源，按 (arrival_tick, task_id) 顺序重试已到达未放置任务，逐请求刷新候选。无可行卡暂时等待且允许后面的可行请求先放；独立也放不下任何卡的请求记 unschedulable。选卡后立即按 row-major-free-v1 预留全部核位、内存与声明均值；即使卡在长轮次中也可预留空核，新任务在下一卡轮次边界实际开始。映射不迁移，实际末步共同屏障完成才释放；卡忙不会冻结其它卡。

集群区分 arrival_tick、placement_tick、requested_start_tick、actual_start_tick 与 completion_tick。五基线requested_start_tick=placement_tick、主动偏移为0；STPS requested_start_tick=placement_tick+d；资源等待=placement-arrival，边界等待=actual_start-requested_start，另记主动等待d，执行时间=completion-actual_start+1，端到端=completion-arrival+1。顶层集群 task_summary/manifest 使用此计量，每卡子目录的内部 start_tick=requested_start_tick；不能把子目录时间当 arrival 到完成时延。

例如K=6，某轮最后包第13cycle到达，轮次占3物理Tick、延长2Tick。这表示通信瓶颈降低逻辑步推进速度；路径与串行发包本身也可能耗时超过K，不能将所有延长都归因于多任务干扰。当前仍是预先轨迹重放与SOP记账，未模拟实际神经元服务与接收计算依赖。

## 5. cycle 数据流、反压与守恒

`core_id=y×mesh_x+x`，XY 先沿 X、再沿 Y。Router 有 N/E/S/W/Local 五个输入 FIFO，每个输出按相同顺序确定性轮转。所有仲裁读取 cycle 旧态，容量检查使用旧队列占用，同 cycle 释放的空间下一 cycle 才可重用；之后原子提交移动。因此不会因 Python 遍历顺序在一个 cycle 穿过多跳。

源 NI 有空位时，生成请求在当前边界入队，记录 source_enqueue；剩余请求留在逻辑待发区。源 NI→本地 Router 成功记 Tx，邻接 Router 转发记 link，目标 Router→目标 NI 记 Rx，目标 NI 释放记 consume。初始 source_enqueue 时间是 offer 边界；转移、发送后补队时间是提交边界。中途经过某核 Router 不记该核的端点 Tx/Rx。

每 hop 占用整个 cycle，提交后 flit 位于下游输入。在整数采样边界没有额外 Link 库存，链路占用由 link 事件计数。目的服务周期 P 默认 1，仅在 P、2P…提交边界消费此前已在 sink 的包；新 Rx 最早下一 cycle 消费。满接收缓冲逐跳施加反压。

每个任务在每 Tick 末校验：

$$
G=Tx+S,\qquad Tx=Rx+N,\qquad Rx=Consumed+R.
$$

G 是生成的跨核 flit；S 是源待发+源 NI，N 是 Router 在网库存，R 是已到达目的 NI 的库存。本地自环单列。全卡屏障释放条件为 S+N=0，而非 R=0；不满足时跨 Tick 等待。库存保留任务/逻辑步/边身份；不丢弃、不重传，不用 Tx−Rx 解释丢包。

## 6. 可观测性与负载定义

### 6.1 当前单卡输出

[metrics](../util/metrics.py) 从同一核记录聚合任务和整卡；计数与时间汇总可由 CSV 复算：

| 文件 | 主键/内容 |
| --- | --- |
| core_tick.csv | task_id、physical_tick、logical_tick、population_id、core_id；SOP、生成、Tx/Rx、等待与末端库存，缺行=零活动 |
| task_tick.csv | 任务/物理 Tick/逻辑 Tick 汇总，包含活跃任务的全零步及旧 sink 消费行 |
| card_tick.csv | 每个物理 Tick 的所有任务汇总、活跃/累计完成数与屏障状态 |
| queue_stats.csv | 每队列每 Tick 占用积分、峰值、样本数、满缓冲 cycle；全零队列可省略 |
| link_stats.csv | 每条有向链路每 Tick 的转发 flit 与忙 cycle |
| step_timing.csv | 每任务每逻辑步的起止 Tick、持续/延长 Tick、最后 Rx 和完成标志 |
| task_summary.csv | 实际启动/完成、执行/端到端时间、extension/slowdown、累计计数、库存、精确 p95 延迟与平均等待 |
| outstanding.csv | 最终各位置的剩余 flit；delivery_pending 区分未到达与已 Rx |
| manifest.json / scenario.json | 参数、来源、单位、输入 hash、固定映射、状态、耗时/峰值库存；工作负载路径解析后的本机场景副本 |
| events.csv | --trace 时记录生成、入队、Tx/link/Rx/consume 与受阻位置/原因 |
| report.html / workload.svg | 各 Task 时变 SOP、发包/收包、MicroPopulation 热图与拥塞图 |

计算 `compute_sops` 是开始该逻辑步时一次记账的 SOP。`expected_tx / expected_rx` 是该步**已量化整数计划量**；原始浮点期望在 Workload 中。正常执行的 generated_tx 等于 expected_tx；两者与实际 Tx/Rx 分列，跨 Tick 等待和截断时可看到未注入或未到达量。后续尚未开始步骤不伪造为已生成。

源等待 `source_wait_cycles_sum / tx_injected` 从生成到注入**完成**，包括无竞争的一个注入 cycle。Rx 延迟从生成到目的 NI 到达完成，保留延迟和与接收数；p95 使用整数 cycle 稀疏精确直方图。零分母时均值取 0，原始分子、分母仍输出；未注入/未收包单列，避免只看成功包的延迟。

`tx_stall_cycles` 是该源有待发、因 Local 输入满而不能注入的 cycle；`rx_blocked_cycles` 是目标 NI 满导致的接收受阻 cycle。`router_wait_flit_cycles` 每个未获转发的旧 Router 输入队头每 cycle 记一次，标记 arbitration 或 downstream_full；归于该包源 MicroPopulation 便于追踪。它可在 tx_stall=0 时大于零，不能当成源核停滞比率。

队列积分在每 cycle 起点采样，samples=K，平均占用=occupancy_sum/samples；峰值另外包含最后一次提交边界，避免漏掉恰好 Tick 末的到达。末端 pending_tx/in_network/pending_rx/sink_unconsumed 是库存，不是跨 Tick 累计吞吐；pending_rx 与源/在网库存是同一批未到达包的目的视图，不能再相加。

### 6.2 已实现多卡窗口指标

计算和通信分开定义：

$$
C_{m,t}=\sum_{c\in m}Q_{m,c,t},\qquad
L_{m,t}=\sum_{c\in m}(Tx_{m,c,t}+Rx_{m,c,t}),
$$

$$
y_m(W)=\sum_{t\in W}C_{m,t},\qquad x_m(W)=\sum_{t\in W}L_{m,t}.
$$

y 的单位SOP；x的单位是端点flit事件。一个跨核包贡献Tx和Rx各一次，两端属于同一卡。跨物理Tick排队使本Tick生成/Tx/Rx不必相等，并推迟下一逻辑步SOP与发包；累计完整运行保持守恒。实际端点均衡须与坐标无关计划需求、任务完成量、执行时间和slowdown并列，避免反压降低每Tick吞吐被误认作均衡改善。

对 x 和 y 分别计算窗口累计均衡：

$$
\bar x=\frac{1}{M}\sum_m x_m,\quad
CV=\frac{\sqrt{M^{-1}\sum_m(x_m-\bar x)^2}}{\bar x},\quad
JFI=\frac{(\sum_m x_m)^2}{M\sum_m x_m^2},\quad
LIF=\frac{\max_m x_m}{\bar x}.
$$

全零该类负载时 CV/JFI/LIF 均取 0，同时保存分子/分母及零负载标记。计算与通信不相加；逐 Tick 比率均值另名，不能冒充窗口累计均衡。[cluster_metrics.py](../simulation/cluster_metrics.py) 已同时输出 full=[1,ticks_executed] 与输入 steady_window 指定的**运行前固定、所有策略一致的稳态窗**，全部配置卡包含空闲卡。为避免平方溢出，ratio 的分子/分母使用 z=x/max(x) 归一尺度；窗口保留各卡原始量和 normalization_scale，可复算上述公式。CV/JFI/LIF 全零取0。

窗口 complete 与 run_complete 分开：已完整观察的时间窗 complete=true，即使任务因 max_ticks 尚未完成；自然终止后余窗补零并记录 padded_zero_ticks。若截断发生在窗口末前，不补未观察时间，complete=false。全程完成量/拒绝/未到达/未完成及已完成样本时延同时输出；有永久拒绝时状态 completed_with_rejections、valid=false，不能冒充完整任务集成功。

集群输出新增 decisions.csv（逐次候选、样本、分数、选择和 mapping）、card_resources.csv（每 Tick 末预留/运行/等待与均值账本）、cluster_tick.csv、balance_windows.json 和顶层带 card_id 的合并表；各卡原始诊断在 cards/card_ID/。顶层均值/包 p95 合并事件计数与精确直方图，不平均每卡分位数。详情见 [metrics.md](metrics.md)，演示见 [cluster_baselines.md](results/cluster_baselines.md)。

## 7. 已实现基线、联合STPS与映射控制

策略公式与生命周期见 [baselines.md](baselines.md)。RR、WorstFit、DRU、BestFit、P2C-Mean 已实现：RR 在全卡编号循环跳过不可行卡；WorstFit/BestFit 分别最大/最小化放置后的剩余核数与内存字典序；DRU 最小化放置后核/内存归一利用率的最大值；确定性同分用最小 card_id。P2C-Mean 从可行卡无放回抽两张，比独立计算与通信均值的归一最大压力，自身 random.Random(seed) 与到达采样分离。无候选不推进 RR 或随机流。

集群请求显式声明 mean_compute_sops、mean_noc_endpoint，单位为 SOP/理想逻辑步与端点事件/理想逻辑步；加载器校验声明均值有限非负，不强制等于重放的真实均值；两个预算来自 cluster 配置，只作评分尺度。策略不读取实际成功收发或未来逐步 trace；已放置未完成任务的声明均值持续计入卡账本，放置增加、actual completion 移除。演示 profile 人工构造，非独立真实校准数据；真实实验须声明校准来源，用同一测试完整轨迹求均值的实验须标为 oracle。

联合STPS已实现，MeanLoad-Dual仍未实现；精确算法及当前120次同二进制结果以 [algo.md](algo.md) 和[stps_hotspots.md](results/stps_hotspots.md)为准。离线预测指纹与测试Workload分开，调度指纹只保留校准计算总量C[T]及稀疏边流量[T,E]，逐MicroPopulation SOP留在实际Workload，不由突触扇出自动推定NoC包数。实际每轮生成量、剩余源/在网库存与固定mapping下的XY链路需求用于预测轮长，时长代理需要独立网络工作点校准，不把SOP换成NoC周期。

STPS联合搜索全部当前可行卡及其有限启动偏移，各卡只作共同row-major映射预览；同一卡同一预计加入轮次的偏移合并。每个卡＋偏移组合用独立校准指纹、真实已观察进度和库存预测完整已知任务尾部，以新任务完成时间及对已有任务的非负拖慢为主成本，独立计算/通信压力作同成本比较，最终一次提交唯一组合。取消先选卡后相位的顺序规则，以及空间/时间拆分和消融方案，核心思路以[algo.md](algo.md)的联合公式为准。

放置仍立即预留资源；实际开始已增加requested_start门控，已有运行任务不为未到时刻的新任务停卡。主动偏移和被动边界等待分别计量，arrival到完成不漏主动等待。实际轮次释放后重新锚定未来预测，但不重发当前步、不修改已提交偏移或mapping。该设计取代旧“按placement+d+s平移相邻物理Tick、相位只最小化峰值”的早期提案；当前代码已实现上述联合预测与相位，当前实验未证实全面负载均衡优势。

row-major 能使映射规则可复现，不能排除映射对路径和拥塞的影响。未来对照须固定 mesh、路由、输入/到达、容量、测量窗和 mapper 版本，记录每个 Task 的映射；在预先声明的多个映射场景分别运行所有策略。固定选卡/启动时刻后单独重放不同映射，可测路径、等待与屏障延长敏感性；不得挑选最利于某个算法的映射。

坐标无关的 SOP/计划通信均衡支持选卡归因；真实 NoC 拥塞结论限定于给定映射和硬件工作点。跨 Tick 延长会改变后续逻辑步出现时刻；不得把运行上限截短或延迟启动产生的低通信量解释为更均衡。多卡基线和窗口计量已经可运行；STPS/相位与独立合成校准已实现；真实校准、单任务干扰对照、mapping敏感性及更大规模论文验证仍待完成。

均衡结果必须多维报告：累计与滑窗CV/JFI/LIF、P99-to-Mean、Max-to-Mean，以及逐Tick比率时间序列的P95/P99/max。LIF即最大均值比；4卡nearest-rank P99等于Max。通信同时给offered端点需求与实际Tx+Rx，防止反压把热点卡显示成低负载。100ms～1s窗口只有在physical_tick_ms已标定时才能转换，否则按Tick报告。

## 8. TODO plan：模拟器与基线状态

- [x] 显式同源图、独立SOP、确定性量化与固定MicroPopulation mapping。
- [x] 最小NoC：XY、有限NI/Router FIFO、轮转仲裁、反压、任务身份与守恒。
- [x] 固定物理Tick、跨Tick队列、全卡Rx屏障；等待步不重复发包或SOP。
- [x] 实际生命周期准入，核位/内存等待、旧sink身份、max_ticks不完整处理。
- [x] step_timing、任务执行/端到端/启动等待、通信extension/slowdown与延迟/拥塞诊断；见[metrics.md](metrics.md)。
- [x] 所有场景/API/默认值/固定规则见[hyperparam.md](hyperparam.md)。
- [x] 正常、跨Tick、小缓冲、预设错峰和截断的重复运行；当前结果见[elastic.md](results/elastic.md)。
- [ ] 代表性大型真实模型采集和规模评估、逐周期计算/物理参数校准。
- [x] 多卡共享物理时间、独立NoC/屏障，外生到达、待队列、放置预留、实际开始/完成释放。
- [x] 五基线 RR/WorstFit/DRU/BestFit/P2C-Mean，共用 row-major-free-v1 mapper、显式双负载均值与决策日志。
- [x] 四卡、每卡4×4，Poisson/bursty 同任务输入；2×5组合完成24任务978跨核flit，见 [cluster_baselines.md](results/cluster_baselines.md)。
- [x] 全程/固定稳态窗口 CV/JFI/LIF、arrival到完成计量、资源/边界等待分解、完整/截断口径与集群守恒。
- [x] STPS卡＋偏移联合搜索、独立精简profile、有界轮次预测、延迟启动及120组配对实验；结果未全面优于最佳基线。
- [ ] MeanLoad-Dual及真实数据/更大规模预测验证。
- [ ] 独立真实校准/测试轨迹、多随机种子、单任务干扰基准、mapping敏感性和论文实验。
