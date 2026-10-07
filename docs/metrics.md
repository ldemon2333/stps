# 模拟器全部指标与统计口径

对应 [util/metrics.py](../util/metrics.py)、[engine.py](../simulation/engine.py)、[cluster_metrics.py](../simulation/cluster_metrics.py)，运行模式为跨 Tick 排队与全卡 Rx 屏障。超参数见 [hyperparam.md](hyperparam.md)。单卡输出 schema_version=2，多卡顶层为3；联合STPS和五种基线共用相同测量口径。

## 1. 时间、身份与输出表

physical_tick 从 1 开始，每个 Tick 固定 K cycle；cycle 从 0 开始，转移在 c+1 提交。logical_tick 是每任务从 0 开始的逻辑步进度，不能用 physical_tick-start_tick 推导；通信延长时同一逻辑步跨多个物理 Tick。population_id 是工作负载中的 MicroPopulation 索引，core_id 为映射后的物理核。每个包及队列库存保留 task_id/logical_tick/edge_id/src_core/dst_core/generated_cycle。

| 文件 | 粒度与行规则 |
| --- | --- |
| core_tick.csv | task_id、physical_tick、logical_tick、population_id、core_id；任一测量非零才写，缺行=零。原逻辑步库存/延后消费仍归原步 |
| task_tick.csv | task_id、physical_tick、logical_tick；每个正在执行/等待屏障的任务保留全零行；旧 sink 事件可产生其它逻辑步的行 |
| card_tick.csv | 每物理 Tick 一行；测量从 task_tick 求和，包含整个观察窗口的空闲 Tick |
| step_timing.csv | 每任务每已开始逻辑步一行；全卡屏障释放时完成，截断时写当前未完成步 |
| task_summary.csv | 每个任务一行，包括尚未启动者；累计事件、最终库存、时延分位与任务时间 |
| queue_stats.csv | 每队列每物理 Tick；峰值为0的行省略；保留容量和积分分母 |
| link_stats.csv | 每有转发的有向链路每物理 Tick |
| outstanding.csv | 最终按身份和位置分组的真实库存，含未到达和已到达未消费 |
| events.csv | --trace 才写；逐生成/入队/转移/受阻事件，数量 count，可复算局部诊断 |
| manifest.json | 全卡累计/派生/时序摘要、输入来源/参数/hash/实际任务时间与库存峰值 |

core→task→card 测量逐字段求和一致。生命周期字段只在相应级别定义，不作逐核相加。合法跨 Tick Rx 不会改变原 logical_tick；因此同物理 Tick 的一任务可以同时含当前步与旧 sink 消费行。

## 2. 通信、计算与库存：MEASURES 全部字段

| 字段 | 单位 | 计数点/归属 |
| --- | --- | --- |
| compute_sops | SOP | 逻辑步开始一次记账；归执行 MicroPopulation 的核；等待 Tick 不重复 |
| expected_tx | flit | 该步已量化的跨核计划需求，归源核；不是原浮点期望 |
| generated_tx | flit | 该步新生成跨核请求，归源核；当前等于 expected_tx |
| expected_rx | flit | 同一跨核计划需求的目的视图，归目的核 |
| tx_injected | flit | 源 NI→本地 Router 成功转移；归源核 |
| rx_ejected | flit | 目标 Router→目的 NI 成功交付；归目的核，简称 Rx |
| rx_consumed | flit | 目的 NI 释放/消费；归原任务目的核，不重复计 Rx |
| local_flits | flit | 自环/同核流量，绕过 NoC，不计入生成/Tx/Rx守恒 |
| tx_stall_cycles | cycle | 源 NI 有队头但本地 Router Local 输入满；该源本 cycle 加1 |
| rx_blocked_cycles | cycle | 目标 NI 满、目标 Router 有接收请求；每目标每 cycle 加1，归轮转选中的队头任务 |
| router_wait_flit_cycles | flit-cycle | 每个未转发的旧 Router 输入队头每 cycle 加1；归该包源核；不包括所有队尾包等待 |
| source_wait_cycles_sum | flit-cycle | 对已注入包累计 Tx提交cycle-generated_cycle；包含最低1 cycle注入服务 |
| rx_latency_cycles_sum | flit-cycle | 对已Rx包累计 Rx提交cycle-generated_cycle |
| rx_excess_latency_cycles_sum | flit-cycle | 对已Rx包累计实际延迟减(h+2)，h为映射后的曼哈顿hop数 |
| pending_tx | flit，末端库存 | source_pending+source_ni，归源核 |
| in_network | flit，末端库存 | Router 输入FIFO中的在网包，归原源核；当前位置见 outstanding |
| pending_rx | flit，末端库存 | source_pending+source_ni+router，同批未到达包的目的视图，归目的核 |
| sink_unconsumed | flit，末端库存 | 已Rx未消费的目的NI库存，归原目的核 |

前三类期望/生成量只在开始步骤记。量化前的 expected_flits 及量化后 quantized_flits 在 manifest.tasks[].input_totals；差值不算丢包。四个库存字段在 task_summary/manifest.totals 为最终值，不能跨Tick累加成吞吐；pending_rx 与 pending_tx+in_network 重复表示同一批包，不能把四列全相加。

每任务每Tick末检查：

$$
G=Tx+P+S,\quad Tx=Rx+N,\quad Rx=Consumed+R.
$$

P/S/N/R 分别是逻辑待发/源NI/Router/目的NI库存。每个包只有一个实际位置。链路转移完成后位于下游Router，在整数边界无独立链路库存。

## 3. 屏障与逐 Tick 生命周期字段

| 表/字段 | 含义 |
| --- | --- |
| core/task/card.barrier_ready | Tick末全卡未到达量为0；False是继续等待，不是运行错误 |
| task.step_started | 本任务当前逻辑步在此物理Tick开始；旧逻辑步消费行留空 |
| task.step_elapsed_ticks | 此步已占用物理Tick数，含本Tick |
| task.waiting_for_communication | 此任务自己的未到达量>0 |
| task.waiting_for_card_barrier | 自己包已Rx，但其它任务未到达，阻止本任务推进 |
| card.round_started | 当前Tick开始了新全卡轮次 |
| card.round_elapsed_ticks | 此轮从起点到本Tick持续多久；无活跃轮次为0 |
| card.communication_extension_tick | 当前Tick是已开始轮次的第2或以后Tick |
| card.active_tasks | 本Tick参与当前轮次的任务数，包含在本Tick末完成者 |
| card.waiting_tasks | 固定单卡为最早start_tick已到但未实际启动数；多卡子报告为已放置未启动数，包含主动延迟和边界等待 |
| card.completed_tasks | 到本Tick末累计完成任务数 |

全卡一轮的参与任务可能有不同logical_tick。只要一个任务尚有未到达包，所有参与者都留在当前步；不在同一Tick释放屏障后立刻发下一步。每个逻辑步最短1物理Tick。

## 4. step_timing：逐步执行时间

| 字段 | 说明 |
| --- | --- |
| step_start_tick / step_end_tick | 此逻辑步起始Tick、完成Tick；截断步的end为最后观察Tick |
| logical_step_duration_ticks | end-start+1；包含最低一个Tick |
| communication_extension_ticks | duration-1；全卡屏障额外占用，包含自己的通信和其它任务造成的等待 |
| last_rx_cycle | 此任务此步最后Rx的绝对cycle；无跨核包/尚未Rx时为空 |
| rx_complete | 该任务本步全部跨核包已经Rx；可以True但step_completed=False（其它任务仍未到达） |
| step_completed | 全卡屏障已释放；False表示截断的部分观察 |

完成步的时间例：K=6，最后Rx在相对起点第13cycle，全卡最早在第3个物理Tick末释放，duration=3、extension=2。无跨核包也可能随同卡其它任务跨3Tick。

可由last_rx_cycle计算自己的包送达时间（单位cycle）：

$$
D^{last}_{\tau,s}=last\_rx\_cycle-(step\_start\_tick-1)K.
$$

它与全卡屏障释放时刻不同；没有包时不人为设一个Rx事件。

## 5. task_summary/manifest.tasks：任务时间与进度

本节列固定单卡入口的时序字段；在线多卡顶层以arrival为起点，并拆分主动等待，见第10节。各卡子目录保持单卡字段格式，其中start_tick为多卡调度器给出的requested_start_tick。

| 字段 | 含义 |
| --- | --- |
| status | completed / unfinished（已启动未完成） / not_started |
| start_tick | 场景规定最早启动Tick |
| planned_end_tick | start_tick+T-1；只作理想单步一Tick参考，不用于释放资源 |
| actual_start_tick / completion_tick | 实际启动/全卡末步屏障完成的Tick；未发生时为空/null |
| logical_ticks | 输入T，总逻辑步数 |
| steps_started / steps_completed | 已生成过的步数 / 已越过屏障的步数；截断可差1 |
| task_start_wait_ticks | 已启动：actual_start-start；未启动：max(0,last_observed-start+1)，仅已观察等待 |
| observed_execution_ticks | 已启动后观察到的执行Tick数；未启动为0 |
| task_execution_ticks | 完成时completion-actual_start+1；未完成为空，不以0伪造快速完成 |
| task_end_to_end_ticks | 固定单卡完成时completion-start_tick+1；多卡子目录从requested_start计，完整arrival口径见顶层task_summary |
| communication_extension_ticks | 任务参与的延长物理Tick累计；同卡等待也计入 |
| slowdown | 完成时execution/T；未完成为空 |

完成任务有 execution=T+communication_extension，end_to_end=start_wait+execution。仅通信时间变化，SOP不模拟服务周期；即使无竞争，路径长、同源串行发包或K过小也会延长，不能把全部extension都称为多任务冲突。

## 6. 延迟、等待和派生比率

| 字段 | 分子/分母与边界 |
| --- | --- |
| mean_source_wait_cycles | source_wait_cycles_sum / tx_injected，0分母取0 |
| mean_rx_latency_cycles | rx_latency_cycles_sum / rx_ejected，0分母取0 |
| mean_rx_excess_latency_cycles | rx_excess_latency_cycles_sum / rx_ejected，0分母取0 |
| router_wait_per_generated_flit | router_wait_flit_cycles / generated_tx，0分母取0；单位cycle/flit，非队列中所有包的平均等待 |
| rx_latency_p95_cycles | 已Rx包延迟的nearest-rank精确95分位；无Rx取0，仅task_summary输出 |
| rx_excess_latency_p95_cycles | 已Rx包(延迟-h-2)的同口径p95；无Rx取0，仅task_summary输出 |

manifest.derived 为全卡前四个比率。全部原始分子/分母均保留在累计列中。全卡p95不能平均任务p95；需要合并直方图或events重算。截断运行只含已成功注入/接收包，必须同时报告未完成量和状态，防止幸存者偏差。额外延迟剔除基本路径成本，但仍包含批量发包串行等待；要隔离多任务干扰，应比较相同映射的单任务基准。

## 7. 队列、链路、事件与最终库存

queue_stats 的 kind 为 source_pending/source_ni/router/sink_ni；router/port 标位置，边界Router也分配五个输入数组，未用端口始终为0。capacity 是对应队列容量，source_pending为空表示无界。

| 队列字段 | 口径 |
| --- | --- |
| occupancy_sum | K个cycle起点占用求和，flit-cycle |
| samples | 积分分母，固定K；未输出的全零队列也有K个逻辑样本 |
| occupancy_peak | 起点及最后提交边界的最大占用，flit |
| full_cycles | K个起点中occupancy==capacity的次数；无界待发区为0 |

单队列平均占用=sum/samples；满队列比率=full_cycles/samples。全网同类平均需要将省略的零队列补入分母：源/宿/待发为核数×K×Tick数，Router为5×核数×K×Tick数。最后边界峰值不增加积分或full_cycles。峰值大不等于停滞，只能说明积压。

link_stats 的 router/port/next_router 标有向链路；flits 是成功转发量，busy_cycles 是转发占用的cycle数。当前一次传一flit，两者相等；利用率=busy_cycles/K，省略链路行视为0。

events.kind 包含 generate/generate_local/source_enqueue/tx/link/rx/consume/tx_stall/rx_blocked/router_wait。count为数量，wait_cycles只对tx/rx定义；input_port/requested_output/reason定位Router队头等待，reason为arbitration或downstream_full。generate和初次入源NI时间为当前开始边界，转移/补队为结束边界。

outstanding.kind 使用库存位置，count为同身份位置数量；delivery_pending=True表示未Rx，sink_ni为False。空文件（只有表头）表示所有库存为0，不保证所有任务已完成，例如零流量任务截断仍可能unfinished。

## 8. manifest 全卡运行、资源和规模指标

| 路径/字段 | 含义 |
| --- | --- |
| status / valid | completed/True 或 max_ticks/False；跨Tick拥塞本身不算失败 |
| ticks_executed / cycles_executed | 实际观察物理Tick数 / Tick数×K |
| timing.rounds_started / rounds_completed | 有活跃任务的全卡轮次开始/屏障完成数；不是任务步数总和 |
| timing.barrier_blocked_ticks | Tick末有未Rx包的Tick数，包含未完成轮次的首Tick |
| timing.communication_extension_ticks | 实际执行过的全卡延长Tick数；未完成轮次首Tick不算extension |
| timing.tasks_completed / tasks_total | 任务完成分子/总任务数；完成率可复算 |
| timing.task_throughput_per_tick | completed/ticks_executed，task/物理Tick，含启动前空闲与等待 |
| peak_inventory_flits.* | 每cycle边界的全卡source_pending/source_ni/router/sink_ni及undelivered峰值；undelivered=前三类之和的峰值，不是各类峰值相加 |
| elapsed_seconds / cpu_seconds | 场景加载和初始化之后到manifest写出之前的墙钟/进程CPU秒；含CSV与step/task汇总，未含输入加载或HTML绘图 |
| tasks[].input_totals | 原浮点期望flit、量化flit（含自环）与SOP；未开始步仍在输入总量中 |
| tasks[].active_edges | 量化后任一步非零边数，包括活跃自环；不是静态全部连接边数 |
| config/max_ticks/units/source/metadata/hash/mapping | 参数、观测边界、来源、输入校验和与完整固定映射，用于复现，不是性能指标 |

验证脚本 comparison.csv 另含 case、K/缓冲、报告路径以及python_peak_traced_bytes/output_bytes/peak_in_network_flits/peak_undelivered_flits。tracemalloc包住加载至报告，内存为Python分配峰值（非RSS）；进程计时受其开销影响。output_bytes为该场景全部产物大小。可选LIF的scale.json范围仅模型采集后的重放/报告。

## 9. 算法比较建议与当前边界

主比较同时给任务执行/端到端时间、slowdown、通信extension、包平均/p95、Router等待及完成率。固定工作负载、请求到达、卡数、K、缓冲和mapping规则；相位延迟可能降低包等待却增加总完成时间，两者都应报告。max_ticks截断不能与完整运行直接比累计等待，不能用少发包得到虚假均衡。

多卡基线与STPS输出CV/JFI/LIF、P99-to-Mean、Max-to-Mean、预先固定稳态窗口和滑动窗口，见第10节。单卡固定场景只保留完整时间序列，不输出跨卡均衡。STPS预测和真实网络统计见第11节，当前同二进制比较见 [stps_hotspots.md](results/stps_hotspots.md)。

## 10. 多卡基线与 STPS 输出、生命周期

多卡manifest版本3，各卡子目录cards/card_i保持单卡版本2明细。顶层CSV合并前置card_id，core/Router编号都是卡内局部地址；不能在不带card_id时按core_id混合不同卡。所有卡每Tick均采样直到集群结束，空卡与完成后空闲卡也有card_tick行。计算/通信计数含义与单卡一致。

| 任务字段 | 完成/已发生事件口径 | 尚未发生时 |
| --- | --- | --- |
| arrival_tick | 外生到达，六策略共享 | 始终保留输入值 |
| placement_tick | 选卡、提交mapping、立即预留核心和内存 | null |
| phase_offset_ticks | 请求偏移d；五基线为0，STPS为选择结果 | 尚未放置为0 |
| requested_start_tick | placement_tick+d | 尚未放置为null |
| actual_start_tick | requested之后的首个可加入卡轮次起点 | null |
| resource_wait_ticks | placement-arrival | 已到达未放置取max(0,最后观察Tick-arrival+1)；拒绝为0 |
| phase_wait_ticks | 已观察的主动等待；正常完成等于d | 已放置时min(d,最后观察Tick-placement+1)，未放置为0 |
| boundary_wait_ticks | actual_start-requested_start | 已放置未启动取max(0,最后观察Tick-requested_start+1) |
| task_start_wait_ticks | actual_start-arrival | 已到达且未启动取已观察等待；拒绝为0 |
| task_execution_ticks | completion-actual_start+1 | 未完成为null |
| task_end_to_end_ticks | completion-arrival+1 | 未完成为null |

主动等待发生在资源已预留之后，不包含资源队列等待。启动达到requested门槛仍可能被卡内旧轮次阻塞，产生独立的boundary等待。完成任务满足：

$$
T_{e2e}=W_{resource}+W_{phase}+W_{boundary}+T_{execution},\qquad
T_{execution}=T+communication\_extension\_ticks.
$$

执行extension包含同卡屏障等待，执行减速比仍execution/T。实际完成和资源释放由真实NoC屏障决定，不使用预测完成时间。单卡固定场景仍以start_tick计量；多卡指标比较使用顶层文件，避免遗漏arrival至requested期间的等待。

状态包括completed、unfinished（已启动）、placed（已预留未启动）、pending（已到达未放置）、not_arrived（运行截断在到达前）、unschedulable（单任务超卡容量）。整体status为completed、completed_with_rejections或max_ticks，只有全部成功completed才valid=True。不要把未完成时间写0；无完成任务的派生均值虽然取0，completed_task_statistics.samples=0明确无样本。

新增文件：

| 文件 | 内容 |
| --- | --- |
| decisions.csv | 每次放置尝试的物理Tick、policy、place/wait/unschedulable、eligible/sample卡ID、打分、最终card/mapping、需求与离线均值；STPS另记偏移、预测起止和全部实际评估候选 |
| card_resources.csv | 每卡Tick末预留核槽/内存、未完成预留任务、运行/尚未启动任务与均值账本；完成释放后取样 |
| cluster_tick.csv | 各卡物理Tick求和MEASURES和endpoint_events=Tx+Rx、active/waiting/placed_waiting/pending/completed任务数 |
| balance_windows.json | full[1,ticks_executed]与预先固定steady的逐卡SOP、offered/served通信累积，以及CV/JFI/LIF/P99-to-Mean/Max-to-Mean和逐Tick尾部汇总 |
| sliding_balance.csv | 每个完整W Tick滑窗的逐卡计算/offered/served负载和跨卡多维比率；W由场景配置 |
| report.html | 各卡曲线/热图、资源热图、arrival至completion时间线；紫色标主动延迟，单列资源/主动/边界等待；链接各卡详细报告 |

cluster_tick的active_tasks是各卡当前轮次参与数，waiting_tasks来自卡上已放置未启动；placed_waiting_tasks按请求生命周期复算，pending_tasks为已到达未放置。completed_tasks累计。末端库存跨卡求和后只取最后Tick为manifest totals库存，累计事件沿所有Tick求和。

manifest.timing新增tasks_total/completed/rejected/not_arrived/arrived/unfinished、task_throughput_per_tick、ticks_executed、run_complete。makespan_ticks只取已完成任务最大completion，未完成运行不能用它代表整体完工；total_completion_span_ticks仅所有任务成功完成时非空。对task_execution_ticks、task_end_to_end_ticks、resource_wait_ticks、phase_wait_ticks、boundary_wait_ticks、communication_extension_ticks、slowdown分别输出mean_和p95_前缀字段；例如mean_phase_wait_ticks。它们只取已完成任务，completed_task_statistics保留sum/samples/mean_denominator；未完成、拒绝单列不混入成功均值。

card_communication_extension_ticks/card_barrier_blocked_ticks/card_rounds_started/active_card_ticks在全卡所有物理Tick上累积，是card-Tick，不是集群时钟长度。不能把所有卡extension相加冒充每个任务extension。

窗口各卡x可取计算SOP、offered通信`2×generated_tx`或served通信`Tx+Rx`，包含所有空闲卡。分别定义：

$$
\bar x=\frac1M\sum_mx_m,\quad CV=\frac{\sqrt{M^{-1}\sum_m(x_m-\bar x)^2}}{\bar x},\quad JFI=\frac{(\sum_mx_m)^2}{M\sum_mx_m^2},\quad LIF=\frac{\max_mx_m}{\bar x}.
$$

实现先除以normalization_scale=max(x)稳定计算，保留所有原始逐卡累积，以及每个比率的value/numerator/denominator/zero_denominator、mean_load/zero_load。全零时三比率均0；计算与通信独立，不相加。full是实际观察窗，因此窗口complete可以True而整个run_complete=False；steady若在截断点之后尚未观察则complete=False、不补0。自然终止后才能将未来steady余Tick补0，padded_zero_ticks明确记录；若有unschedulable任务，run_complete只表示无剩余待处理，overall valid仍False。

全局mean source/Rx/excess和Routerwait per generated由总分子/总分母计算。全局Rx/excess p95由内存中各任务精确直方图合并，不平均卡级分位。manifest.conservation保留四个守恒残差及valid；窗口内Tx-Rx是起止在网库存差，不代表丢包或卡间通信。

六策略比较使用Poisson/bursty、24/48任务和5个配对种子，输入来自独立校准/验证/测试的合成小图。SOP仍没有服务周期；计算均衡是工作量分配指标，不能据执行时间认定计算加速。row-major一致也不排除不同空核形状的路由影响；报告保存每任务mapping供复核。

## 11. STPS 决策、预测误差与实验产物

STPS只用离线SchedulingFingerprint和当时已观察的任务进度、队列库存做预测。profile中的expected_edge_flits与compute_total_sops是需求预测；本页MEASURES仍来自测试Workload的实际生成与NoC事件。profile均值不是实际吞吐，也不替代均衡统计。

| manifest/任务字段 | 说明 |
| --- | --- |
| stps | 本次d_max/gamma/max_rounds/objective/balance_slack/权重；基线为null。adaptive开关由场景配置控制，各卡manifest的adaptive_ledger块记录实际校正是否发生 |
| scheduling.attempts | 非永久拒绝任务的放置尝试次数，包括资源暂满的wait |
| scheduling.seconds / mean_seconds | 资源筛选、策略评分、提交和决策行构建的墙钟秒数 / attempts；无尝试取0。CSV实际写出在计时之外 |
| scheduling.evaluated_candidates | STPS全部尝试实际评估的卡＋去重偏移候选数；基线为0 |
| scheduling.delayed_tasks | 选择phase_offset_ticks>0的任务数，包括截断时仍未启动者 |
| scheduling.phase_offset_ticks_sum | 所有选定偏移d之和；截断时可大于已观察phase_wait_ticks之和 |
| tasks[].profile_path / profile_sha256 | 独立预测文件路径与内容hash；用于关联校准来源，未使用profile的基线任务为null |
| predicted_actual_start / predicted_completion | 放置决策时预测的实际开始/完成物理Tick；只用于观测，不控制真实释放 |
| start_prediction_error_ticks | actual_start-predicted_actual_start；正数表示比预测更晚，未开始为空 |
| completion_prediction_error_ticks | completion-predicted_completion；正数表示比预测更晚，未完成为空 |

predicted字段及两类误差同时出现在顶层task_summary.csv和manifest.tasks。预测依据是放置时已知尾部，之后新到达任务可能改变共卡通信，因此误差同时包含指纹/时长代理误差和后续任务干扰。基线没有生成这些预测，字段为空。

decisions.csv的STPS rows包含phase_offset_ticks、predicted_actual_start、predicted_completion、candidates。candidates是JSON数组；scores也保存同一候选列表，eligible_card_ids仍表示当时资源可行卡，sampled_card_ids为空。相同预测加入边界的d会合并，所以列表不是所有整数d的重复展开。每个候选字段为：

| 候选字段 | 单位与口径 |
| --- | --- |
| card_id / delay / mapping | 候选卡、请求偏移、共同mapper给出的固定物理核列表 |
| predicted_actual_start / predicted_completion | 此候选的预测起止物理Tick |
| completion_cost | 新任务预测完成-current_tick+1，包含候选主动/边界等待 |
| externality_ticks | 对当前卡已有任务逐个求max(0,插入后预测完成-未插入预测完成)再求和 |
| J | completion_cost+externality_ticks；选择的首要目标 |
| peak_comp / peak_noc | 预测已知尾部各轮的SOP峰值 / 跨核需求端点峰值；peak_noc也包含当前未完成轮次的残余端点需求 |
| pressure | max(peak_comp/compute_budget_sops,peak_noc/noc_budget_endpoint)；J同分时比较 |
| predicted_rounds | 此候选预测的尾部轮次数，含开放轮次的残余部分 |
| predicted_residual_ticks | 当前开放轮次还需占用的预测物理Tick数；没有开放轮次为0 |
| predicted_existing_completions / baseline_existing_completions | 插入/不插入新任务时，现有task_id到预测完成Tick的字典 |

来自源pending/NI的一个未到达包还需Tx和Rx，残余端点量计2；已经在Router中的包只剩Rx，计1；sink库存已Rx，不再计端点需求，但占用目的容量。候选peak_noc是预测需求峰值，不等于某个真实物理Tick的tx_injected+rx_ejected。`objective=completion`按(J,pressure,delay,card_id)选择；`objective=balance`先保留J不超过相对slack的候选，再按投影双负载均衡、J、pressure、delay、card_id选择。

[compare_stps.py](../script/compare_stps.py)生成冻结输入与旧累计比较产物；当前多维热点结果由[compare_stps_hotspots.py](../script/compare_stps_hotspots.py)生成，结果解释见 [stps_hotspots.md](results/stps_hotspots.md)。

| 产物 | 指标及范围 |
| --- | --- |
| plan.json / input_hashes.json | 仿真前固定的工作点、种子、样本划分、分析规则及输入hash；不是性能结果 |
| calibration/calibration.csv | 独立校准组合的实际清空cycle、需求代理及比例；仅其非零需求比例拟合gamma |
| calibration/validation.csv、summary.json | 独立验证的known_flow、mean_profile、residual三类cycle/轮长误差；all和active分别汇总，防止大量零步掩盖预测误差 |
| raw.csv | 每次运行的实际状态、计数、时间、均衡及分子/分母、调度开销；两个prediction_error字段分别附_mae/_bias/_samples |
| summary.json / summary.csv | 每到达模式、任务数、策略的有效次数、均值、样本标准差；无效运行保留，不混入完整运行均值 |
| paired.csv | 同模式/任务数/seed的STPS与各基线差值、改善方向、胜/平/负与有效配对数；零基线不算相对百分比 |
| best_baseline.csv | 每个指标按跨种子均值选出的最佳观测基线及STPS配对差值；基线名称随指标改变，不构成额外策略 |
| index.html | 比较汇总与内嵌SVG图；各运行manifest可追溯实际任务与候选 |

验证error为预测减真实，cycle_mae/round_mae是绝对误差均值，cycle_bias/round_bias为有符号均值，round_exact/under/over_count保留分子；cycle_p95_absolute_error使用NumPy分位插值。这与在线任务误差“真实减预测”的方向相反，不能直接合并。初始新轮次验证sink为空，残余验证读取执行K cycle后的库存；这些验证不等于所有运行期队列组合的准确性保证。

比较必须同时给计算与通信均衡、端到端与主动等待、实际通信extension/包等待及完成率。CV越小、JFI越接近1、LIF越接近1表示对应窗口更均衡；全零窗口按0输出且zero_load=True，需要排除空工作量的误读。五个种子的配对统计是本合成工作点的描述，不能据此推导真实模型或所有负载下的优势。

## 12. P99、最大均值比与高频滑动窗口

全程累计CV会掩盖短时热点。每个时间口径现在同时输出CV、JFI、LIF、P99-to-Mean和Max-to-Mean。LIF就是最大均值比：

$$
LIF=\frac{\max_m x_m}{\bar x}.
$$

P99采用跨卡nearest-rank。只有4张卡时P99等于最大卡，所以P99-to-Mean与Max-to-Mean相同；卡数增加后两者可分离。全零负载时比率取0，保留分子/分母和zero标记。

通信分为offered_endpoint_events=2×generated_tx与实际endpoint_events=tx_injected+rx_ejected。固定窗、截断和拥塞诊断优先同时看offered与served，避免反压使成功收发低而误判为轻载。

每个full/steady窗口的temporal_hotspots先逐Tick计算跨卡CV/JFI/LIF/P99-to-Mean/Max-to-Mean，再对这条时间序列输出samples、mean、P95、P99和max。time_card_distribution合并窗口内全部card×tick样本，输出load P99/mean和max/mean。自然结束只在窗口complete时补零，截断不补未观察时间。

sliding_balance.csv实现固定宽度滑动窗口：对每个结束Tick，先聚合每卡[t−W+1,t]内的SOP、offered通信和served端点量，再计算跨卡多维指标。只输出完整W样本，不用启动阶段的短窗口冒充固定窗口。manifest.sliding_windows按W和负载汇总比率时间序列的mean/P95/P99/max；默认W=1/4/8 Tick，场景sliding_window_ticks可配置。

若芯片已标定physical_tick_ms，可通过sliding_window_ms声明100/500/1000ms等窗口，转换为ceil(window_ms/physical_tick_ms)个Tick；没有物理Tick时长时禁止使用毫秒窗口，只报告Tick窗口。CV曲线的陡峰表示阶段性跨卡不均衡，还需与库存、Router wait、任务E2E和完成率一起判断是否形成实际straggler。
