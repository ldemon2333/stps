# 新 NoC 架构下的 baseline 实现与后续设计

状态：**RR、WorstFit、DRU、BestFit、P2C-Mean 与在线多卡执行已实现**。策略在 [schedule/baselines.py](../schedule/baselines.py)，入口是 [cluster_engine.py](../simulation/cluster_engine.py)。各卡共用物理时间、独立 NoC 与卡内 Rx 屏障；资源在放置时预留、实际完成时释放。联合STPS已实现并完成独立校准对比，MeanLoad-Dual仍待实现。历史总量 FIFO 和旧调度实现已删除，当前模块使用新的快照接口与生命周期。

相关文档：[架构](arch.md)、[STPS设计](algo.md)、[超参数](hyperparam.md)、[指标](metrics.md)。4 卡、每卡 4×4、Poisson/bursty 两类到达的演示与验证见 [cluster_baselines.md](results/cluster_baselines.md)。

两条入口保留不同输入责任：单卡 --scenario 提供固定 mapping/start_tick；多卡 --cluster-scenario 提供 arrival_tick 和声明的均值 profile，由策略在线选卡、共用 mapper 放置。

```bash
python main.py --cluster-scenario examples/cluster/poisson.json --policy RR --output-dir data/new_cluster_run --trace
python script/validate_cluster.py --output-root data/cluster
```

## 1. 审查结论：需要改的细节

以下保留历史设计审查及在新架构下的处理。容量、均值选择和实际资源生命周期已落地；逐步预测与相位相关项仍约束未来 STPS 实现。历史源码只作为设计审计依据，不作为新实现入口。

| 旧设计/实现 | 新架构中的问题 | 本文处理 |
| --- | --- | --- |
| RR循环扫描can_host，成功后移动指针 | 思路可保留；can_host不能再只查总神经元和历史内存 | 按空闲物理核槽与每MicroPopulation容量过滤，实际完成释放；放置失败不推进指针 |
| BestFit对空闲神经元/内存取max | 实际选择剩余空间最大的卡，与BestFit装箱名称不符 | 原意改名WorstFit；真正BestFit已作为独立容量装箱对照实现 |
| DRF对放置后神经元/内存比例取max，再选择最小卡 | 是主导资源利用率放置，缺租户份额、公平分配机制；神经元总量也不能代替占用核槽 | 改名DRU：Dominant Resource Utilization，按核槽与内存计算，不声称经典DRF公平性 |
| P2C比较epoch load加新任务平均流量 | 使用了指纹均值，不能称完全fingerprint-blind；低实际吞吐可能是严重反压造成 | 使用独立计算/通信标量计划压力；名称P2C-Mean，不直接以成功Tx/Rx少作为轻载证据 |
| 用placement/start加T安排完成和释放 | 拥塞延长逻辑步，同卡屏障拖慢所有任务 | 以actual completion释放；已经自己Rx完成的任务仍需末步共同屏障通过 |
| 直接把每个逻辑步放在相邻物理Tick预测 | 当前logical_tick显式推进，同一步可能跨多个Tick | 预测分为理想计划与实际进度，屏障释放后更新预测锚点 |
| 用delay>0或固定周期错峰即视为有效相位 | 多个请求时刻可能落入同一个物理屏障等待段，实际同时启动 | 记录requested/actual start与实际加入的卡轮次，合并等效相位候选 |
| 用旧bw_max、每卡FIFO排队比例评价拥塞 | 新模型有源NI/Router/宿NI、路径与反压，无单卡硬带宽池 | 使用任务时间、extension、包延迟和Router/端点等待，完整流量与截断同时报告 |

经典DRF针对多个用户的多资源公平分配；本项目当前没有用户份额模型，不能把一个选卡打分器等同DRF。[Ghodsi等原论文](https://www.usenix.org/legacy/event/nsdi11/tech/full_papers/Ghodsi.pdf)

## 2. 已实现与待实现的对照组合

当前命令支持五个基线：RR、WorstFit、DRU、BestFit 读取容量，P2C-Mean 读取容量与独立计算/通信均值。BestFit 为本轮新增的正式对照。后续 MeanLoad-Dual 与 STPS 设计保留在同一表中，状态单列。

| 策略标识 | 决策信息 | 选卡原则 | 主动相位 | 状态 |
| --- | --- | --- | --- | --- |
| RR | 核槽、内存 | 可行卡轮询 | d=0 | 已实现 |
| WorstFit | 核槽、内存 | 最大剩余容量 | d=0 | 已实现 |
| DRU | 核槽、内存 | 最小放置后主导资源利用率 | d=0 | 已实现 |
| BestFit | 核槽、内存 | 最小可行剩余容量 | d=0 | 已实现 |
| P2C-Mean | 容量、双负载均值 | 随机选两张可行卡，挑平均计划压力较轻者 | d=0 | 已实现 |
| MeanLoad-Dual | 容量、双负载均值 | 在所有可行卡中挑平均计划压力较轻者 | d=0 | 待实现 |
| STPS | 容量、独立校准逐步预测与实际进度 | 全部卡＋偏移联合搜索，按(J,pressure,d,id)统一比较 | 有界d | 已实现 |

MeanLoad-Dual是重要强对照：与STPS使用相同计算/通信来源，只丢弃时序形状。若STPS超过容量策略却没有超过它，不能认定逐步指纹或相位具有收益。

已实现的 BestFit 用于观察集中装箱对通信的代价；可选 P2C-Resource 仍待实现，其设计用 DRU 分数做两选一，单独检验随机候选数量的影响。不要将同名算法的不同信息口径混在一列。

## 3. 所有策略共用的生命周期

以下生命周期已在 [cluster_engine.py](../simulation/cluster_engine.py) 与 [card_runtime.py](../simulation/card_runtime.py) 实现，复用单卡 NoC 与逻辑进度。每个物理 Tick 让所有卡各执行 K cycle；一张卡的长轮次只阻止本卡逻辑推进。卡间不发包，一个任务完整运行在一张卡。未来相位项另行标明。

### 3.1 区分到达、放置、请求启动与实际启动

单卡固定场景的 start_tick 是最早启动与端到端计量起点；集群输入以 arrival_tick 起算，并在顶层 manifest/task_summary 中记录以下字段：

| 字段 | 定义 |
| --- | --- |
| arrival_tick | 外生任务到达时刻，所有策略共用同一输入 |
| placement_tick | 找到当前资源可行卡并提交映射的时刻 |
| phase_offset_ticks | 五基线固定0；STPS选择d，schema顶层stps.d_max控制上限 |
| requested_start_tick | 当前等于 placement_tick；后续相位设计为 placement_tick+d |
| actual_start_tick | 达到请求时刻且该卡处于轮次边界时实际开始 |
| completion_tick | 该任务末步在所属卡的共同Rx屏障释放时完成 |

相应启动等待分解：

$$
W^{resource}=placement-arrival,\qquad
W^{phase}=d,\qquad
W^{boundary}=actual\_start-requested\_start.
$$

$$
T^{exec}=completion-actual\_start+1,\qquad
T^{e2e}=completion-arrival+1=W^{resource}+W^{phase}+W^{boundary}+T^{exec}.
$$

以上计量已由集群引擎和 [cluster_metrics.py](../simulation/cluster_metrics.py) 输出，五基线d=0，STPS可主动延迟。已完成任务满足上述时间分解；未完成任务的 execution/end_to_end/slowdown 留空，并记录已观察等待，不能计成0时延。每卡子目录沿用单卡计量：内部 start_tick=requested_start_tick，因此论文端到端比较应读取顶层 arrival 到 completion 的字段。

### 3.2 当前资源、固定映射与预留

定义卡m总核槽R_m=mesh_x×mesh_y、内存B_m。任务τ需r_τ=MicroPopulation数N与b_τ=state_size_mb。每个pop_size必须不超过单核容量，不以sum(pop_size)估计占几个核。

每次放置只考虑此刻有足够空核槽和内存的卡；被占用资源包括运行任务与已放置但尚未启动的任务。选卡后共用固定row-major空核mapper，以population_id顺序选core_id递增空位，原子提交完整mapping与内存预留。屏障边界等待占已预留资源，所有算法使用相同规则；不在未来虚构空位或提前释放。未实际启动任务不发包、不记SOP，其声明均值已进入计划压力账本。STPS主动相位也遵守同一预留契约，requested_start门控实际开始。

集群 mapper 版本为 row-major-free-v1：按 population_id 顺序取递增可用 core_id，坐标为 x=core_id%mesh_x、y=core_id//mesh_x。放置立即提交核位/内存/均值预留；卡忙时 actual_start_tick 可晚于 placement_tick。单卡固定场景仍使用输入 mapping，在实际启动时占用资源；两种准入入口不混用。

共同mapper只做可行性，不搜索路由。统一row-major不消除mapping影响：不同选卡历史留下不同空核形状，应记录mapping并在预先声明的多个mapper场景重复所有策略。容量基线不根据路径长短重排MicroPopulation。

### 3.3 事件顺序与待任务

每物理Tick起点先应用上一Tick实际完成释放，再纳入本Tick到达，处理按(arrival_tick,task_id)排序的待任务。每放置一个任务后，更新资源与对应策略状态，再处理下一个；同Tick不能所有任务读取同一未更新快照。

资源暂不可用则保留全局待队列，下个Tick重试；前任务不能放置时允许后面的可行任务先放，所有策略共用这一规则。任务独立也放不下任一卡则明确unschedulable，不能悄悄丢弃。卡已在通信轮次中不阻止在空核上预留任务，但新任务必须等轮次边界实际开始。d=0仅表示没有主动相位，仍可能有资源/边界等待。

选卡和mapping一旦提交不迁移、不重新选卡。只有实际末步共同屏障完成才释放预留与负载账本；释放在该 Tick 结束时提交，下一 Tick 起点可重新放置。超过max_ticks记未完成/尚未启动，不能冒充成功。目的NI旧包可在核复用后继续消费并造成新任务反压，保留原身份。

## 4. 已实现：容量基线的精确定义

记已占/预留核数U_m、内存V_m；候选集合为当前资源可行卡。所有确定性平局用card_id递增。

### 4.1 RR

按card_id排序，持久指针初始0。从指针开始循环找第一张可行卡；成功后指针移至所选卡后一张。无可行卡时任务等待，指针不变。完成事件只改变候选资格，不重置指针。全部卡同容量且任务都可放时，选择顺序应为0,1,…,M−1,0。

### 4.2 WorstFit

按以下字典序选择最大项，最后用最小card_id破同分：

$$
\left(R_m-U_m-r_\tau,\ B_m-V_m-b_\tau\right).
$$

先选择放置后剩余核槽最多者，再看剩余内存。它是容量分散规则，不读取SOP/flit或任务剩余执行时间。此规则在同构卡上延续旧“BestFit”最大空闲容量的意图，但改用真正占用的核槽并更正名称。

### 4.3 DRU

$$
S^{DRU}_m=\max\left(\frac{U_m+r_\tau}{R_m},\frac{V_m+b_\tau}{B_m}\right),\qquad
m^*=\arg\min S^{DRU}_m.
$$

核槽与内存均归一化，不加入未建模的“突触容量”或将核占用当计算SOP。高计算/高通信任务和静默任务若请求资源相同，分数相同，正是该容量基线的设计边界。

### 4.4 BestFit

选择上面WorstFit字典序的最小可行剩余容量，形成紧凑装箱；与WorstFit分开命名。核利用集中可能提高、NoC竞争也可能加重，这属于被测结果，不预先认定哪个更好。只在异构容量研究中另定义归一化装箱版本，不沿用同名但变化的公式。

## 5. 平均负载基线与P2C

### 5.1 已实现：两类声明均值与计划账本

从校准指纹定义逐逻辑步计算C_τ(s)与跨核端点需求N_τ(s)=2F_τ(s)，再保留两个标量：

$$
\mu^C_\tau=\frac1{T_\tau}\sum_s C_\tau(s),\qquad
\mu^N_\tau=\frac1{T_\tau}\sum_s N_\tau(s).
$$

计算单位SOP/理想逻辑步，通信单位端点事件/理想逻辑步。当前 [cluster_scenario.py](../simulation/cluster_scenario.py) 要求每个请求显式提供 mean_compute_sops/mean_noc_endpoint，校验有限非负值；不会自动从待执行 Workload 的未来步骤求均值，也不要求声明均值与重放真实均值相等。演示是人工声明的 synthetic profile，非独立真实校准集，也不验证预测精度。真实实验只用校准集统计或明确声明的预测数据，不能读取当前测试请求未来的完整真实发包量。当前重放预先知道输入是模拟机制，不赋予所有在线调度器测试轨迹先知权限；使用同一完整轨迹预测时须命名为oracle实验。

卡上已运行与已预留未完成任务集合A_m的标量计划压力为：

$$
\bar C_m=\sum_{j\in A_m}\mu^C_j,\qquad
\bar N_m=\sum_{j\in A_m}\mu^N_j.
$$

每任务放置时增加、实际完成时移除。即使当前卡在延长轮次、SOP/新生成为0，仍保留它的工作负载均值。不能用最近Tick成功收发少、核计算记账为0，推断卡处于空闲。均值是计划负载代理，不等于当前物理Tick实测服务率；不承诺峰值或延迟预测。

加入新任务后的共同分数为：

$$
S^{mean}_m=\max\left(\frac{\bar C_m+\mu^C_\tau}{B^C_m},\frac{\bar N_m+\mu^N_\tau}{B^N_m}\right).
$$

当前 B^C/B^N 来自 cluster.compute_budget_sops/noc_budget_endpoint；STPS使用相同预算。它们是评分尺度，无网络丢包/拒绝阈值，也不是任意路径的物理吞吐上限。不能把两类原始量直接相加。

### 5.2 已实现：P2C-Mean

从当前可行卡均匀无放回抽两张，取S_mean较小者，card_id破平局；只有一张可行卡就选它。没有可行卡则等待，不消耗随机抽样。使用策略独立、运行前记录seed的随机数流，避免与任务到达/工作负载采样共用状态。

两选一的候选抽样方式参考[Power of Two Choices综述](https://www.eecs.harvard.edu/~michaelm/postscripts/handbook2001.pdf)；本文双负载评分是项目适配，不声称具有原队列模型的性能保证。旧P2C使用一维通信均值，此处独立加入计算与端点通信，且不使用已删除cluster_epoch_loads。

### 5.3 待实现：MeanLoad-Dual与可选P2C-Resource

MeanLoad-Dual在所有可行卡中最小化同一S_mean，平局card_id；它与P2C-Mean只差候选枚举范围。P2C-Resource保留两卡抽样，但使用S_DRU，不读取任何流量指纹。结果需以完整名字区分。

容量/均值打分每候选只用资源快照和缓存标量；维护账本需在放置/实际完成时更新。不要声称整个准入模型大小无关：mapper仍需处理N个MicroPopulation，预测STPS也有窗口开销。当前 decisions.csv 记录候选、抽样、分数、选择、mapping，card_resources.csv 在各 Tick 末记录资源与均值账本；manifest.scheduling已记录完整准入尝试累计耗时/均值和候选数，包含无资源重试；尚未拆出纯打分耗时。

## 6. 已实现：卡＋偏移联合STPS

最新设计以 [algo.md](algo.md) 为准。对每张当前资源可行卡作同一row-major映射预览，再搜索该卡全部有限偏移。合并同一卡落在同一预计加入轮次的偏移，逐组合预测新任务及已有任务的完成时间；所有卡的组合一起比较，最终只提交一个组合的核位、内存与请求启动时刻。

统一主成本为“新任务从placement到完成的预计时间＋对所在卡已有任务造成的非负完成延长”；同成本比较独立计算/通信归一化压力，随后按较小偏移和card_id破平局。这里不先选卡，也不创建STPS-Spatial、顺序版本或基线加Phase包装器，不安排空间/时间拆分与消融。

Offline指纹为联合搜索提供独立校准的逐逻辑步SOP、稀疏边flit和网络时长校准；与候选mapping、卡当前逻辑进度和真实未Rx库存结合，估计突发重叠和轮次时长。只用两个均值可作P2C-Mean等基线，但不能辨识突发错开。预测覆盖完整已知尾部，策略不能读取未来测试流量。

放置时立即预留资源，requested_start门控实际开始；卡上已有任务继续运行，arrival到完成包括资源、主动偏移和边界等待。Dmax=0只是同一联合算法的合法参数边界，不另建立消融。当前schema支持任务profile与stps.d_max/gamma/max_rounds，五基线仍为d=0。STPS使用独立SchedulingFingerprint，profile存在时基线声明均值也由该profile派生；缺profile不运行STPS。

完整STPS已与RR/WorstFit/DRU/BestFit/P2C-Mean完成20个场景、120次同二进制热点重跑；MeanLoad-Dual仍是可选未实现基线。结果见[stps_hotspots.md](results/stps_hotspots.md)：尚未得到稳定更好的计算均衡或所有窗口的通信均衡，不能把联合搜索当成效果保证。评估保持相同输入/网络/mapper规则，同时报告offered/served通信、滑窗热点、完成量、延迟与任务端到端。

## 7. 实验口径与公平性

主实验固定任务到达与测试输入、卡数/容量、K、三类缓冲、sink服务周期、mapper版本和观察窗。不同策略可产生不同mapping/实际开始，因此记录每任务结果，不宣称统一mapper已消除路径影响。随机P2C跨多个预先固定seed比较，并与相同外生输入配对。

建议分两类实验：

- **完整任务集**：停止外生到达后继续运行到全部完成或共同max_ticks；比较arrival到完成平均/p95、执行时间、slowdown、通信extension、任务吞吐、平均/p95包延迟与Router/端点等待。未完成者明示，不计成0时延；已完成样本统计与完成率并列。
- **固定物理时间窗**：所有卡包括空闲卡、所有Tick包括零流量Tick，计算SOP与Tx+Rx分别累积、分别算CV/JFI/LIF。全程和预先固定稳态窗同时给分子/分母、窗口完整标志。自然结束后的余窗补零；若 max_ticks 早于窗口末，不补未观察时间，窗口标为不完整。截断时已完整观察的窗口仍标 complete=true，同时 run_complete=false，不能将窗口完整解释为任务集已完成。

通信均衡定义仍是**各独立卡的卡内端点活动均衡**，无卡间消息。窗口实际Tx+Rx会因跨Tick队列、相位和完成时间改变；同时报告计划需求/完整任务工作量与生成、Tx、Rx、源/在网尾量。平均/分位不由卡级分位平均而来，需合并样本/直方图。

主拥塞解释使用router_wait、源等待、rx_blocked和队列占用；时间改善使用端到端、执行与extension。extension也包含路径、同源串行和同卡屏障，隔离多任务干扰应做相同mapping的单任务重放。不以K或缓冲改变产生的收益称算法收益。

计算只记SOP、不模拟逐周期服务：可以验证计算工作分配是否均衡，不能用当前执行时间声称计算服务加速。相位减少包等待也可能增加启动等待和集群makespan；不能只展示拥塞图或只与最差容量策略比较。旧论文BestFit/DRF/P2C表格与FIFO拥塞百分比全部需在新模式重跑并更名。

## 8. 落地状态与验收

| 顺序 | 工作 | 当前状态与证据 |
| --- | --- | --- |
| 1 | 共享物理时间、独立每卡NoC/屏障、实际释放 | 已实现；test_cluster.py 验证卡独立推进、容量等待、实际完成释放 |
| 2 | 共用候选资格、row-major mapper、放置预留、实际开始 | 已实现；屏障等待期间仍预留资源，实际开始只在本卡轮次边界；STPS主动相位由requested_start门控 |
| 3 | RR/WorstFit/DRU/BestFit/P2C-Mean | 已实现；test_baselines.py 手算公式/平局/失败指针/随机可重复；MeanLoad-Dual 待实现 |
| 4 | 独立均值输入、账本与实测指标 | 已实现显式输入与保留至完成的账本；演示为人工 synthetic profile，真实校准/测试分离待实验 |
| 5 | 联合STPS、校准预测器/延迟 | 已实现；精简独立profile、路径/步投影缓存、有界sink服务、完整尾部、共同gamma、等效轮次与requested_start门控 |
| 6 | 完整任务集、固定窗与新结果 | 已实现集群统计；4卡×4×4、2到达×5策略均完成24任务978跨核flit，见结果记录；论文与mapping敏感性实验待完成 |

[当前结果](results/cluster_baselines.md) 来自同一组合成任务集的 Poisson/bursty 到达和单个策略随机种子，验证在线执行与守恒。它不能代替多种子、真实模型、预测精度或论文效果实验。单卡固定场景与手动错峰继续用于网络机制诊断。
