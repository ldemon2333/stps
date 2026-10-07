# 单卡多任务 SNN NoC 仿真器

当前实现 [RFC 第一阶段](docs/rfc.md)：在单卡二维 mesh 上运行显式指定的多个 SNN Task，输入保存逐 MicroPopulation 的 SOP 与有向边逐 Tick 流量。核映射和启动时刻由场景提供。

每个物理 Tick 固定 K 个 NoC cycle。通信未全部到达目的 NI 时保留队列、跨 Tick 继续传输；卡上任务停在当前逻辑步，全部 Rx 后下一物理 Tick 才共同推进。SOP 与请求只生成一次，拥塞使逻辑步与任务实际运行时间变长。目的 NI 消费与 Rx 分开。

## 运行与观察

```bash
pip install -r requirements-dev.txt
python -m pytest -q tests/
python script/validate_single_card.py --output-root data/phase1
```

最后一条命令创建带时间戳的目录，运行正常、跨 Tick、小缓冲、预设错峰和上限截断场景。打开输出目录的 index.html，即可看每个 Task 的 SOP/发包/收包曲线、MicroPopulation 热图与拥塞；workload.svg 可独立分享。

当前结果见 [跨 Tick 验收记录](docs/results/elastic.md)。配置默认值与全部参数见 [hyperparam.md](docs/hyperparam.md)，所有计数、任务时间与减速比定义见 [metrics.md](docs/metrics.md)。

```bash
python main.py --scenario examples/single_card/positive.json --output-dir data/my_run --trace
python main.py --scenario examples/single_card/carry_over.json --output-dir data/my_carry_over --trace
```

输出目录必须不存在或为空。成功退出码为 0；max_ticks 截断为 2；非法输入在生成结果前拒绝。--trace 写逐 cycle 转移事件，默认写聚合记录。单卡入口固定mapping；多卡入口提供在线策略。无后台服务或外部日志上传。

## 实现结构

| 路径 | 职责 |
| --- | --- |
| [fingerprint/workload.py](fingerprint/workload.py) | 显式有向边 Traffic 与独立 SOP、JSON/NPZ、量化 |
| [fingerprint/dtdg.py](fingerprint/dtdg.py)、[extractor.py](fingerprint/extractor.py) | 指定模型模块的前向采样与显式拓扑转换 |
| [simulation/scenario.py](simulation/scenario.py) | 场景、固定核位与单任务容量校验 |
| [simulation/noc.py](simulation/noc.py) | XY、有限 FIFO、轮转仲裁、反压和原子 cycle |
| [simulation/engine.py](simulation/engine.py) | 物理 Tick、弹性逻辑步、实际资源准入、全卡屏障与守恒 |
| [util/metrics.py](util/metrics.py)、[simulation/report.py](simulation/report.py) | CSV、计数对账、HTML/SVG |
| [examples/single_card/](examples/single_card/) | 三任务稀疏时变输入和固定场景 |

详细字段、时序与验收见 [架构文档](docs/arch.md)、[实验说明](docs/experiment.md)；联合 STPS 实现与结果见 [算法文档](docs/algo.md)。旧调度器、每卡总量 FIFO、E-only 指纹与旧 Q0/Q1/Q2 执行脚本已移除；[当前结果索引](docs/results/README.md)只保留新模拟器可复核的三组结果。

后续算法对照的定义、命名、共用准入和相位实验细节见 [基线设计](docs/baselines.md)。RR、WorstFit、DRU、BestFit、P2C-Mean现已实现；联合STPS与延迟启动已实现，使用独立校准profile；没有空间/时间拆分版。

## 输入与验证边界

示例的 SOP 与 flit 是明确标注的合成数值，展示静默、短突发、计算高而通信低及共享链路争用，不声称执行真实 SNN 推理或 CIM 芯片。SOP 是每逻辑步的工作量记账，未模拟计算服务时间；NoC cycle 尚未做硅片校准。

```bash
python -m fingerprint.cli synthetic --out npz/sparse.json --seed 17
python -m fingerprint.cli from-tensor --tensor trace.npy --pop-size 64 64 --state-size-mb 1 --source model/checkpoint/sample --out npz/graph.npz
```

trace.npy 必须是 (T,N,N,2)，分别为同源边流量期望和目标 SOP。E-only NPZ 会被拒绝。也可用 collect_spike_traces 与 workload_from_spike_traces 接入明确的模型模块、图边和操作倍率；不会按模块注册顺序猜测真实连接。大型模型/真实数据集的端到端采集尚未验证。

可选真实小型 LIF 前向采样入口为 script/profile_tiny_snn.py（需要 torch 与 SpikingJelly）。它以受控合成电流运行未训练模型，已验证采样、同源工作负载导出和单卡重放；输入不是实测数据集，SOP 仅覆盖显式算子范围。

论文源码在 [article/article.tex](article/article.tex)。旧表格使用旧网络模型，本轮没有改写其数值；后续算法实验须重新生成。


## 四卡基线模拟

```bash
make cluster-validate
python main.py --cluster-scenario examples/cluster/poisson.json --policy RR --output-dir data/my_cluster_run --trace
```

统一4张4×4卡、row-major空核映射，分别比较Poisson和bursty到达下的五种策略。每组24个完整任务、8逻辑步，SOP/通信输入人工构造。程序在线选卡、放置预留，卡上逻辑步独立受各自Rx屏障控制；不会因某卡拥塞停止其它卡。

输出顶层report.html、任务时序、资源/决策CSV、全程与固定稳态窗计算/通信均衡，并链接每卡详细报告。参数、预测均值与到达过程见[示例说明](examples/cluster/README.md)，本次结果见[四卡验收](docs/results/cluster_baselines.md)。


## 联合STPS热点对比

```bash
make stps-compare
make stps-hotspots
```

第一条命令生成独立校准/验证/测试输入，第二条在冻结的20个测试场景上用同一当前二进制重跑五基线与STPS，共120组。固定4卡×4×4、Poisson/bursty、24/48任务、5种子；预测指纹只保留逐步计算总量与稀疏边流量，profile与真实重放输入分开。

热点/straggler评价输出计算、offered通信、served通信的CV/JFI/LIF、P99-to-Mean、Max-to-Mean，以及1/4/8 Tick滑动窗口时间序列。保留的热点产物使用`objective=balance,balance_slack=0,adaptive_ledger=True`；当前源码默认关闭adaptive账本，复现该产物必须显式启用。

当前热点重跑见[报告](data/stps_hotspots/20261007T100702_647954Z/index.html)和[分析](docs/results/stps_hotspots.md)。120次运行全部完成且守恒；结果随窗口长度变化，部分4/8 Tick场景改善，另一些退化。逐Tick4卡P99/LIF常饱和为4，计算均衡四组均未胜出，因此没有形成全面优势。
