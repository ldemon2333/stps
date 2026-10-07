# 跨 Tick 队列与弹性逻辑步验收

日期：2026-10-07（Asia/Shanghai）。当前源码保留每物理Tick的K cycle，未Rx包跨Tick排队；全卡屏障让逻辑步和任务执行时间随通信变长。参数/全部指标见[hyperparam.md](../hyperparam.md)、[metrics.md](../metrics.md)。

## 1. 复现与产物

```bash
make validate
python script/validate_single_card.py --output-root data/elastic
/root/miniconda3/envs/snn/bin/python script/profile_tiny_snn.py --output-root data/elastic_lif
```

本次[报告索引](../../data/elastic/20261007T065154_967948Z/index.html)、[comparison.csv](../../data/elastic/20261007T065154_967948Z/comparison.csv)与[跨Tick逻辑步明细](../../data/elastic/20261007T065154_967948Z/carry_over/step_timing.csv)。路径为本地时间戳产物，换机器可生成新目录。

| 场景 | K/缓冲 | 总物理Tick | 全卡延长Tick | Router等待 | 平均包延迟cycle | 完成任务 |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| positive | 40/2 | 8 | 0 | 16 | 9.05 | 3/3 |
| carry_over | 6/2 | 12 | 4 | 16 | 9.05 | 3/3 |
| carry_over_buffer1 | 6/1 | 15 | 7 | 35 | 13.50 | 3/3 |
| carry_over_stagger | 6/2 | 14 | 4 | 0 | 7.14 | 3/3 |
| buffer1 | 40/1 | 8 | 0 | 35 | 13.50 | 3/3 |
| manual_stagger | 40/2 | 10 | 0 | 0 | 7.14 | 3/3 |
| truncated | 6/2 | 2 | 0已执行 | 1 | 5.33已Rx样本 | 0/3 |

所有完整运行保留3731 SOP、22跨核flit和2本地flit，跨核生成=Tx=Rx。truncated只观察前2Tick，累计生成4、Rx3、剩余Router1及sink2；没有把尚未执行的延长Tick记入extension，barrier_blocked_ticks=1。步骤/任务未完成，slowdown和完整执行时间为空。

## 2. 一步如何跨Tick

carry_over的三个任务均实际从Tick1启动；每个8步，全部在Tick12完成，execution=12、extension=4、slowdown=1.5。共同屏障产生同卡等待，compute_heavy仅发1跨核包也会被其它任务拖慢。

| logical_tick | 起始物理Tick | 结束物理Tick | 持续Tick | 延长Tick |
| ---: | ---: | ---: | ---: | ---: |
| 0 | 1 | 1 | 1 | 0 |
| 1 | 2 | 3 | 2 | 1 |
| 2 | 4 | 4 | 1 | 0 |
| 3 | 5 | 5 | 1 | 0 |
| 4 | 6 | 8 | 3 | 2 |
| 5 | 9 | 9 | 1 | 0 |
| 6 | 10 | 10 | 1 | 0 |
| 7 | 11 | 12 | 2 | 1 |

原来“Tick5产生15个包”的突发是逻辑步4；跨Tick等待后现在发生在物理Tick6。生成边界为30，bursty最后Rx在48，共18cycle，所以全卡在Tick8末释放；物理Tick7/8不会重复生成这15个包或该步SOP。compute_heavy在34已收完、fan_in在44已收完，仍等bursty。这些身份与时序可在step_timing/events核对。

缓冲深度降到1时，三个任务均执行15Tick、extension=7、slowdown=1.875。保持K=6，仅改变缓冲，更多反压转成可见任务延长。Router等待35、源停滞36，对比缓冲2的16/6。

## 3. 错峰并不保证更快

carry_over_stagger虽然Router等待降为0、平均包延迟9.05→7.14，但总完成Tick12→14；全卡extension仍4。剩余延长来自路径、同源批量串行和Tick取整，不是每种等待下降都足以跨越下一个K边界。预设fan_in=3、compute_heavy=2，实际分别4/4启动，因前一轮屏障未释放。

完整任务slowdown仍1.5，但请求时刻到完成的端到端时间分别12/13/12Tick；启动等待、任务执行与整场景makespan必须同时报告。这是固定场景对照，不是STPS效果，当前没有加入调度算法。

## 4. 测试与范围

回归涵盖：跨Tick包不丢失/重复、SOP只记一次、显式逻辑进度、全卡等待零包任务、空闲核新任务也需等轮次边界、核/内存按实际完成释放、旧sink身份和跨Tick反压、精确边界Rx、上限截断、不完整时间留空、core/task/card库存与计数对账、报告进度图、相同输入可复现。

该产物生成时，两个Python环境的阶段性完整测试均为 **91 passed**，`make validate`同时重现全部七个场景。本次文档审计时，当前完整套件在两个环境均为 **274 passed**。小型真实LIF前向采样重放亦完成：[报告](../../data/elastic_lif/20261007T064609_136728Z/run/report.html)。两任务各8步，共32flit、32算子SOP代理，K=64时无extension；模型未训练、输入合成电流，只证明采集至重放路径。

comparison包含CPU/墙钟、Python分配峰值、在网峰值和输出大小。耗时只计初始化后网络/CSV阶段，tracemalloc峰值含加载和HTML，非RSS。这些小图结果不支持大型模型性能或真实芯片时序结论。
