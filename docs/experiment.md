# 跨 Tick 通信延长实验

> 多卡热点实验应同时报告CV/JFI/LIF、P99-to-Mean和Max-to-Mean，并使用1/4/8 Tick或已标定100ms～1s的完整滑动窗口；全程累计CV不能单独代表阶段性straggler。

当前单卡模式：每物理Tick执行固定K cycle；包未全部Rx时顺延，逻辑步与同卡其它任务共同等待，全部Rx后下一Tick开始下一步。实验检验同一工作量的延长与等待，参数/指标完整定义见[hyperparam.md](hyperparam.md)、[metrics.md](metrics.md)。

## 1. 复现

```bash
make validate
python script/validate_single_card.py --output-root data/elastic
python main.py --scenario examples/single_card/carry_over.json --output-dir data/my_elastic_run --trace
```

输出目录必须新或空。completed退出0，max_ticks截断退出2，非法输入在运行前报错。矩阵脚本对预期截断也返回0。每次生成UTC时间戳目录与index.html、comparison.csv及场景产物；当前结果见[elastic.md](results/elastic.md)。

## 2. 相同稀疏工作负载

三任务8逻辑步，bursty mapping=[0,7]、fan_in=[1,3]、compute_heavy=[4,6]，4×2网格。总计算3731 SOP，跨核22 flit、本地2 flit。bursty与fan_in路径共享1→2、2→3，初始同启时第5逻辑轮一次生成8+6+1=15个跨核包。

| 场景 | K/缓冲 | 请求启动 | 目的 |
| --- | --- | --- | --- |
| positive | 40/2 | 全部1 | 一个轮次通常可在单Tick内完成 |
| carry_over | 6/2 | 全部1 | 观察跨Tick Rx、任务运行延长 |
| carry_over_buffer1 | 6/1 | 全部1 | 固定K下缓冲更浅、反压更强、延长更多 |
| carry_over_stagger | 6/2 | fan_in=3、compute_heavy=2 | 人工错峰的等待与任务时间代价 |
| buffer1 | 40/1 | 全部1 | 等待增加但K足够大，逻辑步仍可能单Tick完成 |
| manual_stagger | 40/2 | fan_in=3、compute_heavy=2 | 人工起点对照，无算法搜索 |
| truncated | 6/2 | 全部1，max_ticks=2 | 观察未完成步和剩余包，截断不算完成 |

任务请求开始时刻已到也可能因共享屏障/资源而延后；结果按actual_start_tick核对。核位和内存按实际完成释放，不按start+T自动释放。

## 3. 拥塞与减速如何观察

重点看task_execution_ticks、task_end_to_end_ticks、communication_extension_ticks和slowdown，同时看平均/p95包延迟、源等待、Router队头等待、源/宿反压与末端库存。逻辑步进度热图中同一颜色跨多列表示该步被延长。等待物理Tick没有新SOP/生成，但旧包Tx/Rx继续。

K=6时仅无竞争h=4包就最少6cycle，批量串行或竞争能超过一个Tick；extension不全是多任务冲突。固定映射下比较单任务和多任务重放才能隔离干扰。调整K/缓冲属于硬件工作点对照，不是调度算法效果。人工相位错峰可能减少包延迟但延后完成，必须报告两者。

核心/任务/卡测量求和一致；完整运行跨核generated=Tx=Rx，但物理Tick各计数可不同，源和在网库存不能跨Tick当吞吐累加。目的NI消费晚于Rx，结束时sink有库存仍可completed。截断运行的完成时间/slowdown为空，不能用已收到包的平均延迟与完整运行宣称优劣。

## 4. 规模与模型采集边界

comparison.csv记录计数、步骤延长、均值、CPU/墙钟、Python分配峰值、在网/未到达峰值及输出字节。引擎计时不含输入加载/初始化与HTML生成；tracemalloc覆盖加载与报告，非RSS，且影响耗时。性能仅限当前小图。

```bash
/root/miniconda3/envs/snn/bin/python script/profile_tiny_snn.py --output-root data/elastic_lif
```

可选脚本真实执行未训练tiny LIF+Linear，受控合成电流、显式图与同源样本；scale.json只计采集后网络重放。SOP为显式算子代理，未覆盖全部神经元计算。大型模型/数据集、硬件时延尚未验证，联合STPS当前独立比较见第6节；已运行的多卡基线见下一节。


## 5. 四卡baseline对比

```bash
make cluster-validate
python script/validate_cluster.py --output-root data/cluster
python main.py --cluster-scenario examples/cluster/bursty.json --policy P2C-Mean --output-dir data/my_cluster_run --trace
```

五策略RR/WorstFit/DRU/BestFit/P2C-Mean各跑Poisson、bursty，共十组；统一4卡、每卡4×4、row-major空核映射、K=8、三缓冲=2。每到达模式同一组24任务，全部44,880 SOP、978跨核flit和60本地flit。Poisson种子23/rate1.2；bursty Tick1/7/13/19各6个；P2C独立seed17。具体图和参数见[examples/cluster](../examples/cluster/README.md)。

结果包含实际到达/放置/启动/完成与资源/边界等待；完整任务集运行至全部完成，固定steady=[5,40]同时输出。策略不调整mapping优化路径，不共享跨卡队列；卡内全卡屏障和已到达库存归属沿用单卡模型。结果解释应同时看e2e、execution、extension和SOP/端点均衡，不能仅由一个种子的最好指标宣称算法普遍优越。实测表和产物见[cluster_baselines.md](results/cluster_baselines.md)。


## 6. 联合STPS与五基线热点比较

```bash
make stps-compare
make stps-hotspots
```

第一条命令准备独立校准/验证/测试样本；第二条复用冻结的20个测试场景，在同一实验二进制下运行五基线和Balance STPS。配置为4卡×4×4、K8、缓冲2，Poisson/bursty、24/48任务、5seed，共120次运行；gamma为1.4，d_max为4，保留产物中的STPS使用`objective=balance,balance_slack=0,adaptive_ledger=True`。当前源码默认adaptive为False，复现该实验需显式启用。未实现顺序STPS或拆分消融。

本次120/120均完成并守恒，但没有普遍改善负载均衡：累计offered通信只在Bursty48的CV/JFI/LIF三项均胜出，计算CV/JFI/LIF四组均未胜出。STPS改善Poisson24的8-Tick热点和Bursty24/48的4-Tick热点，其他窗口退化。完整数据、指标限制与模型边界见[stps_hotspots.md](results/stps_hotspots.md)。
