# 当前结果索引

本目录只保留与当前固定物理Tick、跨Tick队列、全卡Rx屏障和多卡在线调度代码一致的结果。旧每卡总量FIFO、硬截止模型、Q0/Q1/Q2以及STPS中间版本结果已删除。

| 文档 | 范围 | 使用边界 |
| --- | --- | --- |
| [elastic.md](elastic.md) | 单卡弹性逻辑步、跨Tick队列、小缓冲、手工错峰和截断 | 验证NoC与屏障语义，不比较调度算法 |
| [cluster_baselines.md](cluster_baselines.md) | 四卡五基线的Poisson/bursty单种子功能验收 | 验证在线放置、资源生命周期与守恒，不作为多种子算法结论 |
| [stps_hotspots.md](stps_hotspots.md) | 五基线与Balance STPS的20场景、120次同二进制重跑 | 当前主要比较；同时报告累计、逐Tick和1/4/8 Tick滑窗多维指标 |

结果均来自合成小图。计算SOP是工作量记账，NoC cycle未做硬件时长标定；这些结果不构成真实大型SNN或芯片性能证据。
