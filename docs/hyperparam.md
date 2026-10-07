# 模拟器超参数与配置

本文对应当前“固定物理 Tick、跨 Tick 排队、全卡 Rx 屏障”模式，以及共享该模型的五种多卡基线和联合 STPS。场景输入 schema_version=1；单卡 manifest schema_version=2，多卡顶层 manifest schema_version=3。入口与校验见 [scenario.py](../simulation/scenario.py)、[cluster_scenario.py](../simulation/cluster_scenario.py)、[noc.py](../simulation/noc.py)。字段和观测值见 [metrics.md](metrics.md)。

## 1. 单卡场景参数

| 路径 | 默认值 | 单位/范围 | 作用 |
| --- | --- | --- | --- |
| card.mesh_x | 必填 | 正整数，列数 | 网格水平方向 Router/核数量 |
| card.mesh_y | 必填 | 正整数，行数 | 网格垂直方向 Router/核数量；总核数为二者乘积 |
| card.neurons_per_core | 必填 | 正整数，神经元/核 | 每个 MicroPopulation 的大小上限，一 MicroPopulation 独占一核 |
| card.memory_mb | 必填 | 有限正数，MB | 活跃任务状态内存总预算；不足时待启动任务等待 |
| noc.cycles_per_tick | 必填 | 正整数，cycle/物理 Tick | K；每 Tick 执行 K cycle，未完成通信顺延；改变 K 会改变逻辑步被取整后的持续 Tick 数 |
| noc.router_buffer_depth | 4 | 正整数，flit/输入 FIFO | 每个 Router 的 N/E/S/W/Local 输入各自容量，端口间不共享 |
| noc.source_buffer_depth | 4 | 正整数，flit/核 | 每核源 NI 发送缓冲；多余请求保存在无界计数待发区 |
| noc.sink_buffer_depth | 4 | 正整数，flit/核 | 每核目的 NI 接收缓冲；满时反压 Router |
| noc.sink_service_period | 1 | 正整数，cycle | P；每个核在 P、2P…全局 cycle 提交边界最多消费一个旧 sink flit；新 Rx 不能同 cycle 消费 |
| max_ticks | 10000 | 正整数，物理 Tick | 从物理 Tick 1 开始的运行上限，包括启动前空闲 Tick；未全部完成返回 max_ticks/退出码 2。不是预计任务终点 |

所有整数参数拒绝布尔值；未知字段报错。尚未设置物理 ns、flit bit 宽、逐核 SOP/cycle 服务率、丢包或重传参数。NoC cycle 是模拟时间刻度，不是已标定的异步硬件时钟。

当前示例 mesh=4×2、neurons_per_core=128、memory_mb=16；positive 的 K=40、三个缓冲深度=2。carry_over 的 K=6；其它网络规则相同。

## 2. 任务与工作负载输入

这些是实验条件，主要由图与轨迹决定。

| 字段 | 默认/规则 | 作用 |
| --- | --- | --- |
| tasks | 必填非空数组 | 一个场景的一组完整单卡任务 |
| tasks[].task_id | 必填唯一非空字符串 | 所有包、计数、生命周期的归属 |
| tasks[].workload | 必填 JSON/NPZ 路径 | 相对场景文件解析，可复用同一工作负载 |
| tasks[].start_tick | 必填，正整数 | 最早允许启动的物理 Tick；实际启动可能因屏障、固定核位或内存延后 |
| tasks[].mapping | 必填，N 个互异有效 core_id | mapping[i] 为 MicroPopulation i 的物理核；全程固定；不同任务允许重复核位，由运行时串行占用 |
| pop_size[N] | 必填正整数数组 | 图切片大小；每项不能超过 neurons_per_core |
| edge_src/edge_dst[E] | 必填整数数组 | 静态有向边源宿；自环绕过 NoC；重复源宿边需先合并 |
| edge_expected_flits[T,E] | 必填非负有限数组 | 每逻辑步逐边通信期望；固定按边十进制余量累计 floor，无随机采样 |
| compute_sops[T,N] | 必填非负有限数组 | 独立 SOP 工作量；每逻辑步开始只记一次，延长 Tick 不重复 |
| state_size_mb | 必填有限非负数 | 每任务实例内存，从实际启动至实际完成占用 |
| source/metadata/units | 必填 | 来源与假设；units 必须为 flits_per_logical_tick 和 sops_per_logical_tick |
| schema_version/name | 版本必填 1；场景 name 默认文件名 | 文件契约与报告名称 |

core_id=y×mesh_x+x，坐标为 x=id%mesh_x、y=id//mesh_x。本节单卡固定重放的映射是场景输入。待任务按 (start_tick, task_id) 排序，在全卡轮次边界逐个尝试；前面的任务放不下时允许后面资源可行的任务先启动。等待不会修改固定 mapping。在线多卡入口使用第6节的共同 mapper。

## 3. 固定网络与屏障规则

以下为当前实现常量，无额外 CLI 开关：

- 每核一个 Router，二维有线 mesh、单 VC；单 flit 即单包。
- XY 先 X 再 Y；每个输入/输出/源注入每 cycle 最多一 flit；无等待路径为 h+2 cycle。
- 仲裁顺序 N/E/S/W/Local，成功后推进轮转指针；仲裁读取旧态，同 cycle 释放容量下 cycle 才可重用。
- 全卡同一轮逻辑步全部跨核包完成 Rx 后，下一物理 Tick 才可启动下一轮；任何一个任务的未到达包会阻止其它参与任务推进。
- 初始 SOP/请求集中在该步起始边界；等待时只传旧包。零包步仍占至少一物理 Tick。
- 目的 NI 消费不直接作为屏障条件；其库存可跨步和核复用存活。最后任务完成 Rx 后结束，不额外排空已到达库存。
- 源逻辑待发区无界且批量计数，不等于物理源 NI 容量；路由/NI 队列有界。

## 4. 运行和输出开关

| 接口 | 默认 | 含义 |
| --- | --- | --- |
| main.py --scenario / --cluster-scenario | 二选一必填 | 固定单卡场景 / 在线多卡请求场景 |
| main.py --output-dir | 必填 | 不存在或空目录；拒绝覆盖已有产物 |
| --trace / run_simulation(trace=...) | False | 写完整 events.csv，适合小图诊断；关掉不改变仿真结果 |
| --no-report / run_simulation(report=...) | report=True | 跳过 HTML/SVG 绘图；不影响 CSV/manifest |
| script/validate_single_card.py --output-root | data/phase1 | 创建 UTC 时间戳子目录，运行固定验证矩阵并比较 |
| script/profile_tiny_snn.py --output-root | data/phase1_lif | 可选真实小型前向采样与重放产物位置 |
| Makefile PYTHON / OUTPUT | python / data/phase1 | 执行环境与 demo 根目录，可在 make 命令覆盖 |

报告按物理 Tick 绘图，热图超过 96 Tick 合并连续区间；这只是显示分辨率，CSV 仍保留完整记录。p95 是固定 nearest-rank 0.95 精确稀疏直方图。等待均值零分母取 0；未完成任务的完整执行时间/slowdown 写空值。

## 5. 离线合成与模型采集参数

| API/CLI | 默认/参数 | 作用 |
| --- | --- | --- |
| make_sparse_workload | seed=0、T=8、populations=3、population_size=64、activity_probability=0.12、burst_flits=12 | 可复现的合成稀疏通信和独立 SOP；T/N/size/burst 为正整数，活动概率 [0,1]；不是生物神经元模型 |
| fingerprint.cli synthetic | --seed=0、--ticks=8、--populations=3；--out 必填 | 当前 CLI 只暴露这三个合成参数，其余使用 API 默认 |
| fingerprint.cli from-tensor | --tensor、--pop-size、--state-size-mb、--source、--out 必填 | 导入同源 W[T,N,N,2]；不接受一维聚合 E |
| collect_spike_traces | T/nodes/dataloader 必填，batches=1、reset_fn=None | 明确模块、采集步数和批次数；调用者负责模型/设备与状态复位，不猜拓扑 |
| workload_from_spike_traces | sample_index=None、halo_edges=None、metadata=None；state_size_mb/source 必填 | None 时同批样本取均值；指定整数时双方都用同一样本 |
| split_layer | N_core_cap=4096、K=3、head_dim=32 | 切片容量、fmap 卷积核大小、token_embed 头维度；这里的 K 是卷积核大小，和 NoC cycles_per_tick 无关；需与场景容量一致 |
| EdgeSpec | compute_per_flit=1、delta=0；src/dst/kind/mask_factory 必填 | SOP 算子代理和离线逻辑步移位；delta 不是物理 hop 时延 |
| mask_linear / mask_conv2d / mask_pruned / mask_identity | 目标 size / 卷积核与输出通道 / density / 固定 1 | 指定 spike→边流量倍率；当前密度实现截到 [0,1]；不是网络组播算法 |
| HaloEdgeSpec | 源宿切片与 flits_per_step 必填 | 几何 halo 流量规则，按源放电比例调制；不增加计算通道 |

可选 tiny LIF 脚本固定 seed=17、8 步/2 样本、两层 tau=2 的 LIF 与 Linear4×4(weight=0.7)、受控电流；mesh=2×2、K=64、单核16、内存1 MB，缓冲用默认4、sample0/1映射为[0,3]/[1,2]。这些只是可复现烟测条件，计算范围为下游 Linear SOP，不含全部神经元成本。大模型、数据集、工作负载编码和硬件时延需单独校准。

## 6. 多卡基线与 STPS 入口参数

多卡入口为main.py --cluster-scenario，读取[cluster_scenario.py](../simulation/cluster_scenario.py)定义的请求schema；固定单卡--scenario入口继续使用显式mapping/start_tick。多卡任务完整部署单卡、卡间无消息，各卡NoC和Rx屏障独立推进。

| 字段/开关 | 默认/示例 | 含义 |
| --- | --- | --- |
| cluster.cards | 必填；示例4 | 同构独立卡数量，card_id从0开始 |
| cluster.mesh_x/mesh_y | 必填；4/4 | 每卡网格，不是集群级网格 |
| cluster.neurons_per_core/memory_mb | 必填；64/8 MB | 每核与每卡的资源准入容量，已放置未启动也预留 |
| cluster.compute_budget_sops | 必填正有限数；500 | P2C-Mean均值与STPS预测SOP峰值的归一化尺度，非SOP服务能力 |
| cluster.noc_budget_endpoint | 必填正有限数；40 | P2C-Mean端点均值与STPS预测端点峰值的归一化尺度，非NoC硬吞吐上限 |
| noc.* | 同单卡；示例K=8、缓冲2、消费1 | 所有卡共同网络配置、不同卡队列独立 |
| tasks[].arrival_tick | 必填正整数 | 外生到达；与placement/actual start分别记录，场景不提供mapping |
| tasks[].profile | 基线可省略；STPS必填 | 独立校准的 scheduling_fingerprint JSON 路径，相对场景解析；同模型实例可复用 |
| tasks[].mean_compute_sops/mean_noc_endpoint | 无profile时必填；有profile时默认派生 | 非负有限离线均值，P2C-Mean用于评分；显式填写必须与profile均值一致 |
| steady_window | 可省略；示例[5,40] | 运行前固定的闭区间物理Tick窗口，全部策略一致；同时输出全程窗口 |
| scheduler_seed | 0；示例17 | P2C独立随机流种子 |
| --policy | RR | 精确选项RR/WorstFit/DRU/BestFit/P2C-Mean/STPS |
| --scheduler-seed | 不指定用场景seed | CLI覆盖种子；只用于多卡 |
| max_ticks | 10000；示例400 | 整个集群共享物理时间的上限 |
| metadata | 默认空对象 | 到达生成参数、来源和预测口径，不驱动隐式随机到达 |

五种基线使用d=0；STPS联合搜索候选卡与启动偏移d，任务JSON不直接指定phase_offset。共同mapper固定row-major-free-v1，以MicroPopulation索引对应此刻core_id递增空位；提交mapping时立即预留核心和内存，主动延迟期间也占用，直到实际末步共同屏障完成才释放。无法独立放入任一卡的任务输出unschedulable；临时无资源留在待队列。每Tick同一批到达按(arrival_tick,task_id)串行处理并立即更新资源；无可行卡不消耗P2C随机状态、不推进RR指针。

有profile时，加载器要求pop_size、edge_src、edge_dst、T和state_size_mb与对应测试Workload一致；允许预测流量、SOP与测试轨迹不同。显式均值使用rel_tol=abs_tol=1e-9检查一致性。STPS任一任务缺少profile时，在创建输出目录之前报错。在线策略得到预测profile与已观察进度/队列库存，预测接口不接收测试Workload。

到达生成API make_arrival_ticks(mode,count,seed=0,poisson_rate=1.2,burst_size=6,burst_interval=6)：Poisson由指数间隔取floor(time)+1离散分箱，bursty每burst_interval Tick到达burst_size个，最终批截到count。场景文件保存显式到达，不在每策略执行时重采样。示例count24、Poisson seed23/rate1.2，bursty在Tick1/7/13/19各6个；同一组workload。大小/概率验证以[arrivals.py](../simulation/arrivals.py)实际函数为准。

批量运行script/validate_cluster.py --output-root默认data/cluster，--scheduler-seed默认17，--no-trace默认False（演示默认trace开启）；Makefile cluster-demo/cluster-validate用于原十组基线演示。联合STPS的独立样本对比使用第8节脚本。

## 7. 联合 STPS 与预测指纹

实现见 [schedule/stps.py](../schedule/stps.py)、[fingerprint/scheduling.py](../fingerprint/scheduling.py)，决策过程见 [algo.md](algo.md)。

| 字段/API | 默认/范围 | 作用 |
| --- | --- | --- |
| stps.d_max | 4；非负整数 | 每张资源可行卡搜索d∈[0,d_max]，单位物理Tick；同预测加入边界合并，保留最小d |
| stps.gamma | 1.0；有限数且≥1 | 新轮次和当前残余通信时长共用的离线校准系数；不是硬件时延保证 |
| stps.max_rounds | 10000；正整数 | 单次尾部预测的轮数保护上限；超出报错，不静默裁剪成短预测 |
| build_fingerprint(samples,source,metadata=None) | 非空校准Workload序列；source必填 | 每样本先量化flit，再平均逐边流量；逐样本SOP按MicroPopulation求和后平均 |
| save_fingerprint / load_fingerprint | JSON；schema_version=1，kind=scheduling_fingerprint | 预测文件独立于Workload，禁止两种schema混读 |

指纹保存pop_size[N]、edge_src/edge_dst[E]、expected_edge_flits[T,E]、compute_total_sops[T]、state_size_mb、source、metadata。单位为expected_flits_per_logical_tick、total_sops_per_logical_tick和MB；数组只读，要求有限非负、形状一致且无重复边。self边保留拓扑身份，NoC均值排除self。mean_compute_sops=总SOP/T，mean_noc_endpoint=2×非self期望flit总量/T；这是预测需求，实际Tx/Rx仍从网络事件统计。

多卡与偏移仅改变在线投影；文件不保存“卡数×时间×边数”的[C,T,E]张量，也不保存[T,N]逐核计算副本。一次决策中，同卡的现有尾部和各偏移候选共用XY路径/每步需求缓存。预测完整的已知任务尾部，使用有限目的NI库存与全局消费相位的闭式时长修正，不通过无限sink迭代试探容量。字典序评分为“新任务完成成本+对已有任务的延迟、计算/通信最大归一化压力、d、card_id”。计算仍仅为SOP记账，在完成成本同分时影响压力评分。

placement_tick记录真实提交；TaskPlacement.start_tick在多卡内部表示requested_start_tick=placement_tick+d。CardRuntime在该时刻之后的首个卡屏障边界实际启动。phase_wait_ticks记录已观察的主动等待，boundary_wait_ticks记录requested至actual之间的被动等待；完整端到端时间始终从arrival_tick开始。

## 8. 独立校准与六策略比较脚本

[compare_stps.py](../script/compare_stps.py)在运行前写plan.json，固定数据生成、校准规则和输入；[compare_stps_hotspots.py](../script/compare_stps_hotspots.py)复用冻结测试场景，在同一当前二进制下重跑六策略并输出多维热点指标。热点脚本显式固定`objective=balance,balance_slack=0,adaptive_ledger=True`，不依赖STPSConfig默认值。参数变化应开启新的输出目录；当前分析见 [stps_hotspots.md](results/stps_hotspots.md)。

| 配置/CLI | 默认 | 作用 |
| --- | --- | --- |
| --output-root | data/stps_comparison | 自动创建UTC时间戳子目录 |
| --seeds | 11 23 37 53 71 | 五个配对实验种子；同一场景六策略共用全部到达和测试轨迹 |
| --task-counts | 24 48 | 常规/较高请求量；各自运行poisson和bursty |
| --prepare-only | False | 仅固化计划、校准、验证及测试输入，跳过策略仿真 |
| --no-reports | False | 关闭选定单次运行的详细HTML；CSV与批量汇总仍保留 |
| 网络/资源 | 4卡，每卡4×4，K=8，三个缓冲深度2，消费周期1；64神经元/核、8 MB/卡 | 六策略共用；归一化尺度SOP=500、endpoint=40 |
| 时间上限/窗口 | max_ticks=800；steady_window=[5,40] | 固定物理Tick口径，同时保存全程与固定窗口 |
| 校准/验证样本 | 四类模板各8/4个，合计32/16个 | 各集合独立；校准流量先逐样本量化再平均；每模板T=8 |
| 活动扰动 | 流量与SOP独立逐元素U(0.6,1.4) | 保留模板拓扑与零活动位置，表示合成幅度变化，不是实测SNN |
| 校准分位 | q=0.9 | gamma=max(1,q90(实际清空cycle/需求代理L))，只用非零校准需求；本次冻结配置gamma=1.4，API缺省仍1.0 |
| 常规到达（任务数≤24） | poisson_rate=1.2；burst_size=6、burst_interval=6 | 到达只采样一次后保存为显式请求 |
| 较高到达（任务数>24） | poisson_rate=2.4；burst_size=12、burst_interval=6 | 其它硬件与模板规则不变 |

默认矩阵为2种到达×2种任务数×5个种子×6种策略=120次运行。校准包括能放入16核的单模板与双模板组合，按同一逻辑步同步发包、交替使用起始/末尾连续核段、初始sink为空；验证使用独立样本检验已知流量代理、均值profile预测及K cycle后残余库存预测。gamma规则预先固定，验证集和比较测试结果不参与调参。

种子命名规则：校准1000+100×模板序号+样本序号；验证2000+100×模板序号+样本序号；测试[3000+实验seed,task_index]；任务类别排序[4000,实验seed]。各split、输入hash、源文件hash和实际参数均写入产物。STPS与基线使用同一校准profile的均值；保留未完成运行，完整运行的配对差值与跨种子均值/样本标准差单独汇总。

## 9. 高频热点窗口

STPS另支持`objective=completion|balance`、`balance_slack`（严格相对J容差，默认0.10）、`compute_weight/noc_weight`（正数，默认1）和`adaptive_ledger`（默认False）。冻结v2选择`objective=balance,balance_slack=0`；v3仅将`adaptive_ledger`设True，实验结果退化，因此默认仍False。

| 字段 | 默认 | 含义 |
| --- | --- | --- |
| sliding_window_ticks | [1,4,8] | 完整物理Tick滑动窗口宽度，去重排序；每个结束Tick只在已有完整W样本时输出 |
| physical_tick_ms | 未设置 | 一个物理Tick对应的毫秒数；只有硬件/系统已标定时填写 |
| sliding_window_ms | [] | 例如[100,500,1000]ms，按ceil(ms/physical_tick_ms)转Tick；缺physical_tick_ms时报错 |

Tick窗口与毫秒窗口合并后至少保留一个正整数。W=1等于逐Tick跨卡指标，W>1观察短窗口累积负载。修改窗口只影响统计，不改变调度、发包和任务执行。
