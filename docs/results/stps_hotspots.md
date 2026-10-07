# STPS热点与滑动窗口重跑

日期：2026-10-07（Asia/Shanghai）。为避免用全程平均CV代替阶段性straggler，本实验在同一实验二进制下重跑五基线与最终STPS：20个冻结测试场景×6策略=120次，全部完成。新增计算/offered通信/served通信的CV、JFI、LIF、P99-to-Mean、Max-to-Mean，以及1/4/8 Tick完整滑动窗口。

产物：[报告](../../data/stps_hotspots/20261007T100702_647954Z/index.html)、[raw_hotspot.csv](../../data/stps_hotspots/20261007T100702_647954Z/raw_hotspot.csv)、[summary_hotspot.json](../../data/stps_hotspots/20261007T100702_647954Z/summary_hotspot.json)、[运行计划](../../data/stps_hotspots/20261007T100702_647954Z/plan_hotspot.json)。输入沿用冻结测试workload/profile和到达，STPS使用`objective=balance,balance_slack=0,adaptive_ledger=True`。当次顶层manifest尚未保存adaptive开关，但各卡manifest的非零`adjustment_steps`与校正量证明自适应路径已执行，且plan记录为True。当前源码默认值已改为False；复现本次配置必须显式启用。五基线在同一实验二进制下重跑，所有运行守恒。

4卡时nearest-rank P99就是最大卡，因此P99-to-Mean=LIF=Max-to-Mean。逐Tick稀疏offered通信常只有一张卡非零，所有策略的逐TickP99 LIF常饱和为4，无法区分策略。4/8 Tick窗口更有判别力。

## 累计多维均衡

| 场景 | STPS计算 CV/JFI/LIF | 最佳传统计算 | STPS offered通信 CV/JFI/LIF | 最佳传统通信 |
| --- | --- | --- | --- | --- |
| Poisson24 | 0.3966 / 0.8601 / 1.5847 | WorstFit 0.1782 / 0.9651 / 1.2216 | 0.1092 / 0.9878 / 1.1239 | P2C-Mean CV0.1020；LIF1.1316 |
| Bursty24 | 0.2986 / 0.9144 / 1.3954 | RR 0.1445 / 0.9789 / 1.1899 | 0.2041 / 0.9556 / 1.2865 | BestFit 0.0993 / 0.9892 / 1.1485 |
| Poisson48 | 0.2710 / 0.9263 / 1.3674 | BestFit 0.1927 / 0.9630 / 1.2415 | 0.0950 / 0.9874 / 1.1269 | BestFit 0.0948 / 0.9902 / 1.1074 |
| Bursty48 | 0.1925 / 0.9591 / 1.2211 | DRU/RR各项最佳 | 0.0734 / 0.9941 / 1.0978 | BestFit 0.0866 / 0.9922 / 1.1097 |

STPS只在Bursty48累计offered通信三项均优于最佳传统基线；计算三项四组都未胜出。Poisson24通信CV稍差，但JFI/LIF略优，说明不同指标可能给出不同排序，不能只选CV或只选JFI。

## 高频滑动窗口尾部

下表为跨5seed的“每run滑窗比率P99”的均值，越小越好。

| 场景 | 4-Tick offered CV-P99 / LIF-P99 | 最佳传统 | 8-Tick offered CV-P99 / LIF-P99 | 最佳传统 |
| --- | --- | --- | --- | --- |
| Poisson24 | 1.7321 / 4.0000 | WorstFit 1.6436 / 3.8303 | 0.8762 / 2.3545 | P2C-Mean 1.1060 / 2.8642 |
| Bursty24 | 1.6154 / 3.7600 | RR 1.6456 / 3.8349 | 0.9172 / 2.4571 | RR 0.8123 / 2.2622 |
| Poisson48 | 1.7079 / 3.9579 | BestFit 1.6457 / 3.8460 | 1.1135 / 2.7837 | BestFit/RR 0.9853 / 2.5409 |
| Bursty48 | 1.6417 / 3.8353 | RR 1.7321 / 4.0000 | 1.1194 / 2.8957 | DRU 1.0293 / 2.6438 |

STPS在Poisson24的8-Tick尾部和Bursty24/48的4-Tick尾部有改善；其它短窗口退化。窗口长度会改变结论，说明“降低全程通信CV”不能推出所有实时尺度上的热点减少。

逐TickP99/LIF四组均约4，反映小卡数和稀疏发包导致的饱和。扩展到更多卡后P99与Max才有机会分离；当前4卡实验中两者应同时展示但不视为独立证据。

offered=2×generated避免反压造成低served假象；served=Tx+Rx仍用于执行结果。该实验未加入100ms物理标定，只使用1/4/8 Tick。若要声明100ms～1s，需要先设置physical_tick_ms并按ceil(ms/tick_ms)重跑。
